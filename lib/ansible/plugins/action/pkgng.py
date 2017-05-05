from ansible.plugins.action import ActionBase

from ansible.plugins import connection_loader
from ansible.errors import AnsibleError

class ActionModule(ActionBase):
    def run(self, tmp=None, task_vars=None):

        args = self._task.args.copy()
        chroot = False
        iocage = args.pop('iocage', None)

        if self._connection.transport == 'ssh' and iocage is not None:
            self.ioc_jail = iocage
            chroot = True

        if self._connection.transport == 'sshjail':
            self.ioc_jail = self._connection.jailspec
            self._play_context.remote_addr = self._connection.host
            new_connection = connection_loader.get('ssh', self._play_context, self._connection._new_stdin)
            self._connection = new_connection
            chroot = True

        if chroot:
            args['chroot'] = "/iocage/jails/{}/root".format(self.get_jail_uuid())

        if task_vars is None:
            task_vars = dict()
        result = super(ActionModule, self).run(tmp, task_vars)
        result.update(self._execute_module(module_args=args, task_vars=task_vars))
        return result

    def get_jail_uuid(self):
        jail = self._low_level_execute_command(u"iocage get host_hostuuid {jail}".format(jail = self.ioc_jail))
        stdout = jail['stdout']

        if jail['rc'] != 0:
            raise AnsibleError(u"Unable to determine uuid of iocage: {}".format(stdout))

        return stdout.rstrip()
