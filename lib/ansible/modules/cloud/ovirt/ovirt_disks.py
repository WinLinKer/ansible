#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 Red Hat, Inc.
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import time
import traceback
import ssl

from httplib import HTTPSConnection

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse


try:
    import ovirtsdk4.types as otypes
except ImportError:
    pass

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.ovirt import (
    BaseModule,
    check_sdk,
    check_params,
    create_connection,
    convert_to_bytes,
    equal,
    follow_link,
    ovirt_full_argument_spec,
    search_by_name,
    wait,
)

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'community',
                    'version': '1.0'}

DOCUMENTATION = '''
---
module: ovirt_disks
short_description: "Module to manage Virtual Machine and floating disks in oVirt."
version_added: "2.2"
author: "Ondra Machacek (@machacekondra)"
description:
    - "Module to manage Virtual Machine and floating disks in oVirt."
options:
    id:
        description:
            - "ID of the disk to manage. Either C(id) or C(name) is required."
    name:
        description:
            - "Name of the disk to manage. Either C(id) or C(name)/C(alias) is required."
        aliases: ['alias']
    vm_name:
        description:
            - "Name of the Virtual Machine to manage. Either C(vm_id) or C(vm_name) is required if C(state) is I(attached) or I(detached)."
    vm_id:
        description:
            - "ID of the Virtual Machine to manage. Either C(vm_id) or C(vm_name) is required if C(state) is I(attached) or I(detached)."
    state:
        description:
            - "Should the Virtual Machine disk be present/absent/attached/detached."
        choices: ['present', 'absent', 'attached', 'detached']
        default: 'present'
    image_path:
        description:
            - "Path to disk image, which should be uploaded."
            - "Note that currently we support only compability version 0.10 of the qcow disk."
            - "Note that you must have an valid oVirt engine CA in your system trust store
               or you must provide it in C(ca_file) parameter."
            - "Note that there is no reliable way to achieve idempotency, so
               if you want to upload the disk even if the disk with C(id) or C(name) exists,
               then please use C(force) I(true). If you will use C(force) I(false), which
               is default, then the disk image won't be uploaded."
        version_added: "2.3"
    size:
        description:
            - "Size of the disk. Size should be specified using IEC standard units.
               For example 10GiB, 1024MiB, etc."
            - "Size can be only increased, not decreased."
    interface:
        description:
            - "Driver of the storage interface."
        choices: ['virtio', 'ide', 'virtio_scsi']
        default: 'virtio'
    format:
        description:
            - Specify format of the disk.
            - If (cow) format is used, disk will by created as sparse, so space will be allocated for the volume as needed, also known as I(thin provision).
            - If (raw) format is used, disk storage will be allocated right away, also known as I(preallocated).
            - Note that this option isn't idempotent as it's not currently possible to change format of the disk via API.
        choices: ['raw', 'cow']
    storage_domain:
        description:
            - "Storage domain name where disk should be created. By default storage is chosen by oVirt engine."
    storage_domains:
        description:
            - "Storage domain names where disk should be copied."
            - "C(**IMPORTANT**)"
            - "There is no reliable way to achieve idempotency, so every time
               you specify this parameter the disks are copied, so please handle
               your playbook accordingly to not copy the disks all the time."
        version_added: "2.3"
    force:
        description:
            - "Please take a look at C(image_path) documentation to see the correct
               usage of this parameter."
        version_added: "2.3"
    profile:
        description:
            - "Disk profile name to be attached to disk. By default profile is chosen by oVirt engine."
    bootable:
        description:
            - "I(True) if the disk should be bootable. By default when disk is created it isn't bootable."
    shareable:
        description:
            - "I(True) if the disk should be shareable. By default when disk is created it isn't shareable."
    logical_unit:
        description:
            - "Dictionary which describes LUN to be directly attached to VM:"
            - "C(address) - Address of the storage server. Used by iSCSI."
            - "C(port) - Port of the storage server. Used by iSCSI."
            - "C(target) - iSCSI target."
            - "C(lun_id) - LUN id."
            - "C(username) - CHAP Username to be used to access storage server. Used by iSCSI."
            - "C(password) - CHAP Password of the user to be used to access storage server. Used by iSCSI."
            - "C(storage_type) - Storage type either I(fcp) or I(iscsi)."
extends_documentation_fragment: ovirt
'''


EXAMPLES = '''
# Examples don't contain auth parameter for simplicity,
# look at ovirt_auth module to see how to reuse authentication:

# Create and attach new disk to VM
- ovirt_disks:
    name: myvm_disk
    vm_name: rhel7
    size: 10GiB
    format: cow
    interface: virtio

# Attach logical unit to VM rhel7
- ovirt_disks:
    vm_name: rhel7
    logical_unit:
      target: iqn.2016-08-09.brq.str-01:omachace
      id: 1IET_000d0001
      address: 10.34.63.204
    interface: virtio

# Detach disk from VM
- ovirt_disks:
    state: detached
    name: myvm_disk
    vm_name: rhel7
    size: 10GiB
    format: cow
    interface: virtio

# Upload local image to disk and attach it to vm:
# Since Ansible 2.3
- ovirt_disks:
    name: mydisk
    vm_name: myvm
    interface: virtio
    size: 10GiB
    format: cow
    image_path: /path/to/mydisk.qcow2
    storage_domain: data
'''


RETURN = '''
id:
    description: "ID of the managed disk"
    returned: "On success if disk is found."
    type: str
    sample: 7de90f31-222c-436c-a1ca-7e655bd5b60c
disk:
    description: "Dictionary of all the disk attributes. Disk attributes can be found on your oVirt instance
                  at following url: https://ovirt.example.com/ovirt-engine/api/model#types/disk."
    returned: "On success if disk is found and C(vm_id) or C(vm_name) wasn't passed."

disk_attachment:
    description: "Dictionary of all the disk attachment attributes. Disk attachment attributes can be found
                  on your oVirt instance at following url:
                  https://ovirt.example.com/ovirt-engine/api/model#types/disk_attachment."
    returned: "On success if disk is found and C(vm_id) or C(vm_name) was passed and VM was found."
'''


def _search_by_lun(disks_service, lun_id):
    """
    Find disk by LUN ID.
    """
    res = [
        disk for disk in disks_service.list(search='disk_type=lun') if (
            disk.lun_storage.id == lun_id
        )
    ]
    return res[0] if res else None


def upload_disk_image(connection, module):
    size = os.path.getsize(module.params['image_path'])
    transfers_service = connection.system_service().image_transfers_service()
    transfer = transfers_service.add(
        otypes.ImageTransfer(
            image=otypes.Image(
                id=module.params['id'],
            )
        )
    )
    transfer_service = transfers_service.image_transfer_service(transfer.id)

    try:
        # After adding a new transfer for the disk, the transfer's status will be INITIALIZING.
        # Wait until the init phase is over. The actual transfer can start when its status is "Transferring".
        while transfer.phase == otypes.ImageTransferPhase.INITIALIZING:
            time.sleep(module.params['poll_interval'])
            transfer = transfer_service.get()

        # Set needed headers for uploading:
        upload_headers = {
            'Authorization': transfer.signed_ticket,
        }

        proxy_url = urlparse(transfer.proxy_url)
        context = ssl.create_default_context()
        auth = module.params['auth']
        if auth.get('insecure'):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif auth.get('ca_file'):
            context.load_verify_locations(cafile='ca.pem')

        proxy_connection = HTTPSConnection(
            proxy_url.hostname,
            proxy_url.port,
            context=context,
        )

        with open(module.params['image_path'], "rb") as disk:
            chunk_size = 1024 * 1024 * 8
            pos = 0
            while pos < size:
                transfer_service.extend()
                upload_headers['Content-Range'] = "bytes %d-%d/%d" % (pos, min(pos + chunk_size, size) - 1, size)
                proxy_connection.request(
                    'PUT',
                    proxy_url.path,
                    disk.read(chunk_size),
                    headers=upload_headers,
                )
                r = proxy_connection.getresponse()
                if r.status >= 400:
                    raise Exception("Failed to upload disk image.")
                pos += chunk_size
    finally:
        transfer_service.finalize()
        while transfer.phase in [
            otypes.ImageTransferPhase.TRANSFERRING,
            otypes.ImageTransferPhase.FINALIZING_SUCCESS,
        ]:
            time.sleep(module.params['poll_interval'])
            transfer = transfer_service.get()
        if transfer.phase in [
            otypes.ImageTransferPhase.UNKNOWN,
            otypes.ImageTransferPhase.FINISHED_FAILURE,
            otypes.ImageTransferPhase.FINALIZING_FAILURE,
            otypes.ImageTransferPhase.CANCELLED,
        ]:
            raise Exception(
                "Error occured while uploading image. The transfer is in %s" % transfer.phase
            )
        if module.params.get('logical_unit'):
            disks_service = connection.system_service().disks_service()
            wait(
                service=disks_service.service(module.params['id']),
                condition=lambda d: d.status == otypes.DiskStatus.OK,
                wait=module.params['wait'],
                timeout=module.params['timeout'],
            )

    return True


class DisksModule(BaseModule):

    def build_entity(self):
        logical_unit = self._module.params.get('logical_unit')
        return otypes.Disk(
            id=self._module.params.get('id'),
            name=self._module.params.get('name'),
            description=self._module.params.get('description'),
            format=otypes.DiskFormat(
                self._module.params.get('format')
            ) if self._module.params.get('format') else None,
            sparse=self._module.params.get('format') != 'raw',
            provisioned_size=convert_to_bytes(
                self._module.params.get('size')
            ),
            storage_domains=[
                otypes.StorageDomain(
                    name=self._module.params.get('storage_domain'),
                ),
            ],
            shareable=self._module.params.get('shareable'),
            lun_storage=otypes.HostStorage(
                type=otypes.StorageType(
                    logical_unit.get('storage_type', 'iscsi')
                ),
                logical_units=[
                    otypes.LogicalUnit(
                        address=logical_unit.get('address'),
                        port=logical_unit.get('port', 3260),
                        target=logical_unit.get('target'),
                        id=logical_unit.get('id'),
                        username=logical_unit.get('username'),
                        password=logical_unit.get('password'),
                    )
                ],
            ) if logical_unit else None,
        )

    def update_storage_domains(self, disk_id):
        changed = False
        disk_service = self._service.service(disk_id)
        disk = disk_service.get()
        sds_service = self._connection.system_service().storage_domains_service()

        # We don't support move&copy for non file based storages:
        if disk.storage_type != otypes.DiskStorageType.IMAGE:
            return changed

        # Initiate move:
        if self._module.params['storage_domain']:
            new_disk_storage = search_by_name(sds_service, self._module.params['storage_domain'])
            changed = self.action(
                action='move',
                entity=disk,
                action_condition=lambda d: new_disk_storage.id != d.storage_domains[0].id,
                wait_condition=lambda d: d.status == otypes.DiskStatus.OK,
                storage_domain=otypes.StorageDomain(
                    id=new_disk_storage.id,
                ),
                post_action=lambda _: time.sleep(self._module.params['poll_interval']),
            )['changed']

        if self._module.params['storage_domains']:
            for sd in self._module.params['storage_domains']:
                new_disk_storage = search_by_name(sds_service, sd)
                changed = changed or self.action(
                    action='copy',
                    entity=disk,
                    wait_condition=lambda disk: disk.status == otypes.DiskStatus.OK,
                    storage_domain=otypes.StorageDomain(
                        id=new_disk_storage.id,
                    ),
                )['changed']

        return changed

    def _update_check(self, entity):
        return (
            equal(self._module.params.get('description'), entity.description) and
            equal(convert_to_bytes(self._module.params.get('size')), entity.provisioned_size) and
            equal(self._module.params.get('shareable'), entity.shareable)
        )


class DiskAttachmentsModule(DisksModule):

    def build_entity(self):
        return otypes.DiskAttachment(
            disk=super(DiskAttachmentsModule, self).build_entity(),
            interface=otypes.DiskInterface(
                self._module.params.get('interface')
            ) if self._module.params.get('interface') else None,
            bootable=self._module.params.get('bootable'),
            active=True,
        )

    def update_check(self, entity):
        return (
            super(DiskAttachmentsModule, self)._update_check(follow_link(self._connection, entity.disk)) and
            equal(self._module.params.get('interface'), str(entity.interface)) and
            equal(self._module.params.get('bootable'), entity.bootable)
        )


def main():
    argument_spec = ovirt_full_argument_spec(
        state=dict(
            choices=['present', 'absent', 'attached', 'detached'],
            default='present'
        ),
        id=dict(default=None),
        name=dict(default=None, aliases=['alias']),
        vm_name=dict(default=None),
        vm_id=dict(default=None),
        size=dict(default=None),
        interface=dict(default=None,),
        storage_domain=dict(default=None),
        storage_domains=dict(default=None, type='list'),
        profile=dict(default=None),
        format=dict(default='cow', choices=['raw', 'cow']),
        bootable=dict(default=None, type='bool'),
        shareable=dict(default=None, type='bool'),
        logical_unit=dict(default=None, type='dict'),
        image_path=dict(default=None),
        force=dict(default=False, type='bool'),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )
    check_sdk(module)
    check_params(module)

    try:
        disk = None
        state = module.params['state']
        connection = create_connection(module.params.get('auth'))
        disks_service = connection.system_service().disks_service()
        disks_module = DisksModule(
            connection=connection,
            module=module,
            service=disks_service,
        )

        lun = module.params.get('logical_unit')
        if lun:
            disk = _search_by_lun(disks_service, lun.get('id'))

        ret = None
        # First take care of creating the VM, if needed:
        if state == 'present' or state == 'detached' or state == 'attached':
            ret = disks_module.create(
                entity=disk,
                result_state=otypes.DiskStatus.OK if lun is None else None,
            )
            is_new_disk = ret['changed']
            ret['changed'] = ret['changed'] or disks_module.update_storage_domains(ret['id'])
            # We need to pass ID to the module, so in case we want detach/attach disk
            # we have this ID specified to attach/detach method:
            module.params['id'] = ret['id'] if disk is None else disk.id

            # Upload disk image in case it's new disk or force parameter is passed:
            if module.params['image_path'] and (is_new_disk or module.params['force']):
                uploaded = upload_disk_image(connection, module)
                ret['changed'] = ret['changed'] or uploaded
        elif state == 'absent':
            ret = disks_module.remove()

        # If VM was passed attach/detach disks to/from the VM:
        if module.params.get('vm_id') is not None or module.params.get('vm_name') is not None and state != 'absent':
            vms_service = connection.system_service().vms_service()

            # If `vm_id` isn't specified, find VM by name:
            vm_id = module.params['vm_id']
            if vm_id is None:
                vm_id = getattr(search_by_name(vms_service, module.params['vm_name']), 'id', None)

            if vm_id is None:
                module.fail_json(
                    msg="VM don't exists, please create it first."
                )

            disk_attachments_service = vms_service.vm_service(vm_id).disk_attachments_service()
            disk_attachments_module = DiskAttachmentsModule(
                connection=connection,
                module=module,
                service=disk_attachments_service,
                changed=ret['changed'] if ret else False,
            )

            if state == 'present' or state == 'attached':
                ret = disk_attachments_module.create()
                if lun is None:
                    wait(
                        service=disk_attachments_service.service(ret['id']),
                        condition=lambda d:follow_link(connection, d.disk).status == otypes.DiskStatus.OK,
                        wait=module.params['wait'],
                        timeout=module.params['timeout'],
                    )
            elif state == 'detached':
                ret = disk_attachments_module.remove()

        module.exit_json(**ret)
    except Exception as e:
        module.fail_json(msg=str(e), exception=traceback.format_exc())
    finally:
        connection.close(logout=False)


if __name__ == "__main__":
    main()
