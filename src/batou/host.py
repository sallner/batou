from batou import output, DeploymentError
from batou import remote_core
from batou.update import generate_bootstrap
import os
import sys


# Monkeypatch execnet to support vagrant ssh. Can be removed
# with execnet release 1.4


def new_ssh_args(spec):
    from execnet.gateway_io import popen_bootstrapline
    remotepython = spec.python or 'python'
    if spec.type == 'vagrant':
        args = ['vagrant', 'ssh', spec.ssh, '--', '-C']
    else:
        args = ['ssh', '-C']
    if spec.ssh_config is not None:
        args.extend(['-F', str(spec.ssh_config)])
    remotecmd = '%s -c "%s"' % (remotepython, popen_bootstrapline)
    if spec.type == 'vagrant':
        args.extend([remotecmd])
    else:
        args.extend([spec.ssh, remotecmd])
    return args

import execnet.gateway_io
execnet.gateway_io.ssh_args = new_ssh_args


class RPCWrapper(object):

    def __init__(self, host):
        self.host = host

    def __getattr__(self, name):
        def call(*args, **kw):
            output.annotate(
                'rpc {}: {}(*{}, **{})'.format(self.host.fqdn, name, args, kw),
                debug=True)
            self.host.channel.send((name, args, kw))
            while True:
                message = self.host.channel.receive()
                output.annotate('message: {}'.format(message), debug=True)
                type = message[0]
                if type == 'batou-result':
                    return message[1]
                elif type == 'batou-output':
                    _, output_cmd, args, kw = message
                    getattr(output, output_cmd)(*args, **kw)
                elif type == 'batou-error':
                    raise DeploymentError()
                elif type in ['batou-unknown-error']:
                    output.error(message[1])
                    raise RuntimeError('Remote exception encountered.')
                else:
                    raise RuntimeError("Unknown message type {}".format(type))
        return call


class Host(object):

    def __init__(self, fqdn, environment):
        self.fqdn = fqdn
        self.name = self.fqdn.split('.')[0]

        self.rpc = RPCWrapper(self)
        self.environment = environment

    def deploy_component(self, component):
        self.rpc.deploy(component)

    def roots_in_order(self):
        return self.rpc.roots_in_order()


class LocalHost(Host):

    def connect(self):
        self.gateway = execnet.makegateway(
            "popen//python={}".format(sys.executable))
        self.channel = self.gateway.remote_exec(remote_core)

    def start(self):
        self.rpc.lock()

        # Since we reconnected, any state on the remote side has been lost,
        # so we need to set the target directory again (which we only can
        # know about locally).
        self.rpc.setup_output()

        # XXX the cwd isn't right.
        self.rpc.setup_deployment(os.getcwd(), self.environment.name,
                                  self.fqdn, self.environment.overrides)

    def disconnect(self):
        self.gateway.exit()


class RemoteHost(Host):

    gateway = None

    def connect(self, interpreter='python2.7'):
        if self.gateway:
            output.annotate('Reconnecting ...', debug=True)
            self.gateway.exit()

        self.gateway = execnet.makegateway(
            "ssh={}//python={}//type={}".format(
                self.fqdn, interpreter,
                self.environment.connect_method))
        self.channel = self.gateway.remote_exec(remote_core)

        if self.rpc.whoami() != self.environment.service_user:
            self.gateway.exit()
            self.gateway = execnet.makegateway(
                "ssh={}//python=sudo -u {} {}//type={}".format(
                    self.fqdn,
                    self.environment.service_user,
                    interpreter,
                    self.environment.connect_method))
            self.channel = self.gateway.remote_exec(remote_core)

    def start(self):
        output.step(self.name, 'Bootstrapping ...', debug=True)
        self.rpc.lock()
        env = self.environment

        self.remote_repository = self.rpc.ensure_repository(
            env.target_directory, env.update_method)
        self.remote_base = self.rpc.ensure_base(
            env.deployment_base)

        output.step(self.name, 'Updating repository ...', debug=True)
        env.repository.update(self)

        bootstrap = generate_bootstrap(env.version, env.develop)
        self.rpc.build_batou(env.deployment_base, bootstrap)

        # Now, replace the basic interpreter connection, with a "real" one
        # that has all our dependencies installed.
        self.connect(self.remote_base + '/.batou/bin/python')

        # Since we reconnected, any state on the remote side has been lost,
        # so we need to set the target directory again (which we only can
        # know about locally)
        self.rpc.setup_output()

        self.rpc.ensure_repository(env.target_directory, env.update_method)

        self.rpc.setup_deployment(
            self.remote_base,
            env.name,
            self.fqdn,
            env.overrides)

    def disconnect(self):
        self.gateway.exit()
