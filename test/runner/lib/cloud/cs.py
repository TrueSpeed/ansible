"""CloudStack plugin for integration tests."""
from __future__ import absolute_import, print_function

import os
import time

from lib.cloud import (
    CloudProvider,
    CloudEnvironment,
)

from lib.util import (
    find_executable,
    ApplicationError,
    display,
    SubprocessError,
    is_shippable,
)

from lib.http import (
    HttpClient,
    urlparse,
)

from lib.docker_util import (
    docker_run,
    docker_rm,
    docker_inspect,
    docker_pull,
)

try:
    # noinspection PyPep8Naming
    import ConfigParser as configparser
except ImportError:
    # noinspection PyUnresolvedReferences
    import configparser


class CsCloudProvider(CloudProvider):
    """CloudStack cloud provider plugin. Sets up cloud resources before delegation."""
    DOCKER_SIMULATOR_NAME = 'cloudstack-sim'

    def __init__(self, args):
        """
        :type args: TestConfig
        """
        super(CsCloudProvider, self).__init__(args, config_extension='.ini')

        self.image = 'resmo/cloudstack-sim'
        self.container_name = ''
        self.endpoint = ''
        self.host = ''
        self.port = 0

    def filter(self, targets, exclude):
        """Filter out the cloud tests when the necessary config and resources are not available.
        :type targets: tuple[TestTarget]
        :type exclude: list[str]
        """
        if os.path.isfile(self.config_static_path):
            return

        docker = find_executable('docker')

        if docker:
            return

        super(CsCloudProvider, self).filter(targets, exclude)

    def setup(self):
        """Setup the cloud resource before delegation and register a cleanup callback."""
        super(CsCloudProvider, self).setup()

        if self._use_static_config():
            self._setup_static()
        else:
            self._setup_dynamic()

    def get_remote_ssh_options(self):
        """Get any additional options needed when delegating tests to a remote instance via SSH.
        :rtype: list[str]
        """
        if self.managed:
            return ['-R', '8888:localhost:8888']

        return []

    def get_docker_run_options(self):
        """Get any additional options needed when delegating tests to a docker container.
        :rtype: list[str]
        """
        if self.managed:
            return ['--link', self.DOCKER_SIMULATOR_NAME]

        return []

    def cleanup(self):
        """Clean up the cloud resource and any temporary configuration files after tests complete."""
        if self.container_name:
            if is_shippable():
                docker_rm(self.args, self.container_name)
            elif not self.args.explain:
                display.notice('Remember to run `docker rm -f %s` when finished testing.' % self.container_name)

        super(CsCloudProvider, self).cleanup()

    def _setup_static(self):
        """Configure CloudStack tests for use with static configuration."""
        parser = configparser.RawConfigParser()
        parser.read(self.config_static_path)

        self.endpoint = parser.get('cloudstack', 'endpoint')

        parts = urlparse(self.endpoint)

        self.host = parts.hostname

        if not self.host:
            raise ApplicationError('Could not determine host from endpoint: %s' % self.endpoint)

        if parts.port:
            self.port = parts.port
        elif parts.scheme == 'http':
            self.port = 80
        elif parts.scheme == 'https':
            self.port = 443
        else:
            raise ApplicationError('Could not determine port from endpoint: %s' % self.endpoint)

        display.info('Read cs host "%s" and port %d from config: %s' % (self.host, self.port, self.config_static_path), verbosity=1)

        self._wait_for_service()

    def _setup_dynamic(self):
        """Create a CloudStack simulator using docker."""
        config = self._read_config_template()

        self.container_name = self.DOCKER_SIMULATOR_NAME

        results = docker_inspect(self.args, self.container_name)

        if results and not results[0]['State']['Running']:
            docker_rm(self.args, self.container_name)
            results = []

        if results:
            display.info('Using the existing CloudStack simulator docker container.', verbosity=1)
        else:
            display.info('Starting a new CloudStack simulator docker container.', verbosity=1)
            docker_pull(self.args, self.image)
            docker_run(self.args, self.image, ['-d', '-p', '8888:8888', '--name', self.container_name])
            display.notice('The CloudStack simulator will probably be ready in 5 - 10 minutes.')

        self.host = 'localhost'
        self.port = 8888
        self.endpoint = 'http://%s:%d' % (self.host, self.port)

        self._wait_for_service()

        if self.args.explain:
            values = dict(
                HOST=self.host,
                PORT=str(self.port),
            )
        else:
            credentials = self._get_credentials()

            if self.args.docker:
                host = self.DOCKER_SIMULATOR_NAME
            else:
                host = self.host

            values = dict(
                HOST=host,
                PORT=str(self.port),
                KEY=credentials['apikey'],
                SECRET=credentials['secretkey'],
            )

        config = self._populate_config_template(config, values)

        self._write_config(config)

    def _wait_for_service(self):
        """Wait for the CloudStack service endpoint to accept connections."""
        if self.args.explain:
            return

        client = HttpClient(self.args, always=True)
        endpoint = self.endpoint

        for _ in range(1, 90):
            display.info('Waiting for CloudStack service: %s' % endpoint, verbosity=1)

            try:
                client.get(endpoint)
                return
            except SubprocessError:
                pass

            time.sleep(10)

        raise ApplicationError('Timeout waiting for CloudStack service.')

    def _get_credentials(self):
        """Wait for the CloudStack simulator to return credentials.
        :rtype: dict[str, str]
        """
        client = HttpClient(self.args, always=True)
        endpoint = '%s/admin.json' % self.endpoint

        for _ in range(1, 90):
            display.info('Waiting for CloudStack credentials: %s' % endpoint, verbosity=1)

            response = client.get(endpoint)

            if response.status_code == 200:
                return response.json()

            time.sleep(10)

        raise ApplicationError('Timeout waiting for CloudStack credentials.')


class CsCloudEnvironment(CloudEnvironment):
    """CloudStack cloud environment plugin. Updates integration test environment after delegation."""
    def configure_environment(self, env, cmd):
        """
        :type env: dict[str, str]
        :type cmd: list[str]
        """
        changes = dict(
            CLOUDSTACK_CONFIG=self.config_path,
        )

        env.update(changes)

        cmd.append('-e')
        cmd.append('cs_resource_prefix=%s' % self.resource_prefix)
