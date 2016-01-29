from .environment import Environment, MissingEnvironment
from .utils import locked, self_id
from .utils import notify
from batou import DeploymentError, ConfigurationError
from batou._output import output, TerminalBackend
import sys


class Deployment(object):

    _upstream = None

    def __init__(self, environment, platform, timeout, dirty, fast):
        self.environment = environment
        self.platform = platform
        self.timeout = timeout
        self.dirty = dirty
        self.fast = fast

    def load(self):
        output.section("Preparing")

        output.step("main",
                    "Loading environment `{}`...".format(self.environment))

        self.environment = Environment(
            self.environment, self.timeout, self.platform)
        self.environment.deployment = self
        self.environment.load()

        # This is located here to avoid duplicating the verification check
        # when loading the repository on the remote environment object.
        output.step("main", "Verifying repository ...")
        self.environment.repository.verify()

        output.step("main", "Loading secrets ...")
        self.environment.load_secrets()

    def configure(self):
        output.section("Configuring first host")
        self.connections = iter(self._connections())
        self.connections.next()

    def _connections(self):
        self.environment.prepare_connect()
        for i, host in enumerate(self.environment.hosts.values(), 1):
            if host.ignore:
                output.step(host.name, "Connection ignored ({}/{})".format(
                    i, len(self.environment.hosts)),
                    bold=False, red=True)
                continue
            output.step(host.name, "Connecting via {} ({}/{})".format(
                        self.environment.connect_method, i,
                        len(self.environment.hosts)))
            host.connect()
            host.start()
            yield

    def connect(self):
        output.section("Connecting remaining hosts")
        # Consume the connection iterator to establish remaining connections.
        list(self.connections)

    def deploy(self):
        output.section("Deploying")

        # Pick a reference remote (the last we initialised) that will pass us
        # the order we should be deploying components in.
        reference_node = [h for h in self.environment.hosts.values()
                          if not h.ignore][0]

        for root in reference_node.roots_in_order():
            hostname, component, ignore_component = root
            host = self.environment.hosts[hostname]
            if host.ignore:
                output.step(
                    hostname,
                    "Skipping component {} ... (Host ignored)".format(
                        component), red=True)
                continue
            if ignore_component:
                output.step(
                    hostname, "Skipping component {} ... (Component ignored)".
                    format(component), red=True)
                continue

            output.step(
                hostname, "Deploying component {} ...".format(component))
            host.deploy_component(component)

    def disconnect(self):
        output.step("main", "Disconnecting from nodes ...", debug=True)
        for node in self.environment.hosts.values():
            node.disconnect()


def main(environment, platform, timeout, dirty, fast, check_only):
    output.backend = TerminalBackend()
    output.line(self_id())
    with locked('.batou-lock'):
        try:
            deployment = Deployment(
                environment, platform, timeout, dirty, fast)
            deployment.load()
            deployment.configure()
            if not check_only:
                deployment.connect()
                deployment.deploy()
            deployment.disconnect()
        except MissingEnvironment as e:
            e.report()
            output.section("CONFIGURATION FAILED", red=True)
            if check_only:
                output.section("CHECK FAILED", red=True)
                notify('Deployment check finished',
                       'Configuration for {} encountered an error.'.format(
                           environment))
            sys.exit(1)
        except ConfigurationError as e:
            if check_only:
                output.section("CHECK FAILED", red=True)
                notify('Deployment check finished',
                       'Configuration for {} encountered an error.'.format(
                           environment))
            sys.exit(1)
        except DeploymentError as e:
            e.report()
            notify('Deployment failed',
                   '{} encountered an error.'.format(environment))
            output.section("DEPLOYMENT FAILED", red=True)
            sys.exit(1)
        except Exception:
            # An unexpected exception happened. Bad.
            output.error("Unexpected exception", exc_info=sys.exc_info())
            output.section("DEPLOYMENT FAILED", red=True)
            notify('Deployment failed', '')
            sys.exit(1)
        else:
            if check_only:
                output.section("CHECK FINISHED", green=True)
                notify('Deployment check finished',
                       'Successfully checked configuration for {}.'.format(
                           environment))
            else:
                output.section("DEPLOYMENT FINISHED", green=True)
                notify('Deployment finished',
                       'Successfully deployed {}.'.format(environment))
