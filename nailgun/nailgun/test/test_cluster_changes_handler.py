# -*- coding: utf-8 -*-

#    Copyright 2013 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json

from paste.fixture import TestApp
from mock import Mock, patch
from netaddr import IPNetwork, IPAddress
from sqlalchemy.sql import not_

import nailgun
from nailgun.logger import logger
from nailgun.task.helpers import TaskHelper
from nailgun.settings import settings
from nailgun.test.base import BaseHandlers
from nailgun.test.base import fake_tasks
from nailgun.test.base import reverse
from nailgun.api.models import Cluster, Attributes, IPAddr, Task
from nailgun.api.models import Network, NetworkGroup, IPAddrRange
from nailgun.network.manager import NetworkManager
from nailgun.task import task as tasks


class TestHandlers(BaseHandlers):

    @fake_tasks(fake_rpc=False, mock_rpc=False)
    @patch('nailgun.rpc.cast')
    def test_deploy_cast_with_right_args(self, mocked_rpc):
        self.env.create(
            cluster_kwargs={
                "mode": "ha",
                "type": "compute"
            },
            nodes_kwargs=[
                {"role": "controller", "pending_addition": True},
                {"role": "controller", "pending_addition": True},
                {"role": "controller", "pending_addition": True},
            ]
        )
        cluster_db = self.env.clusters[0]
        cluster_depl_mode = 'ha'

        # Set ip ranges for floating ips
        ranges = [['240.0.0.2', '240.0.0.4'],
                  ['240.0.0.3', '240.0.0.5'],
                  ['240.0.0.10', '240.0.0.12']]

        floating_network_group = self.db.query(NetworkGroup).filter(
            NetworkGroup.name == 'floating').filter(
                NetworkGroup.cluster_id == cluster_db.id).first()

        # Remove floating ip addr ranges
        self.db.query(IPAddrRange).filter(
            IPAddrRange.network_group_id == floating_network_group.id).delete()

        # Add new ranges
        for ip_range in ranges:
            new_ip_range = IPAddrRange(
                first=ip_range[0],
                last=ip_range[1],
                network_group_id=floating_network_group.id)

            self.db.add(new_ip_range)
        self.db.commit()

        # Update netmask for public network
        public_network_group = self.db.query(NetworkGroup).filter(
            NetworkGroup.name == 'public').filter(
                NetworkGroup.cluster_id == cluster_db.id).first()
        public_network_group.netmask = '255.255.255.128'
        self.db.commit()

        supertask = self.env.launch_deployment()
        deploy_task_uuid = [x.uuid for x in supertask.subtasks
                            if x.name == 'deployment'][0]

        msg = {'method': 'deploy', 'respond_to': 'deploy_resp',
               'args': {}}
        self.db.add(cluster_db)
        cluster_attrs = cluster_db.attributes.merged_attrs_values()

        nets_db = self.db.query(Network).join(NetworkGroup).\
            filter(NetworkGroup.cluster_id == cluster_db.id).all()

        for net in nets_db:
            if net.name != 'public':
                cluster_attrs[net.name + '_network_range'] = net.cidr

        cluster_attrs['floating_network_range'] = [
            '240.0.0.10',
            '240.0.0.11',
            '240.0.0.12',

            '240.0.0.2',
            '240.0.0.3',
            '240.0.0.4',
            '240.0.0.5']

        management_vip = self.env.network_manager.assign_vip(
            cluster_db.id,
            'management'
        )
        public_vip = self.env.network_manager.assign_vip(
            cluster_db.id,
            'public'
        )

        cluster_attrs['management_vip'] = management_vip
        cluster_attrs['public_vip'] = public_vip
        cluster_attrs['deployment_mode'] = cluster_depl_mode
        cluster_attrs['deployment_id'] = cluster_db.id
        cluster_attrs['network_manager'] = "FlatDHCPManager"
        cluster_attrs['network_size'] = 256

        msg['args']['attributes'] = cluster_attrs
        msg['args']['task_uuid'] = deploy_task_uuid
        nodes = []
        provision_nodes = []

        admin_net_id = self.env.network_manager.get_admin_network_id()

        for n in sorted(self.env.nodes, key=lambda n: n.id):

            q = self.db.query(IPAddr).join(Network).\
                filter(IPAddr.node == n.id).filter(
                    not_(IPAddr.network == admin_net_id)
                )

            """
            Here we want to get node IP addresses which belong
            to storage and management networks respectively
            """
            node_ip_management, node_ip_storage = map(
                lambda x: q.filter_by(name=x).first().ip_addr
                + "/" + cluster_attrs[x + '_network_range'].split('/')[1],
                ('management', 'storage')
            )
            node_ip_public = q.filter_by(name='public').first().ip_addr + '/25'

            nodes.append({'uid': n.id, 'status': n.status, 'ip': n.ip,
                          'error_type': n.error_type, 'mac': n.mac,
                          'role': n.role, 'id': n.id, 'fqdn':
                          '%s-%d.%s' % (n.role, n.id, settings.DNS_DOMAIN),
                          'progress': 0, 'meta': n.meta, 'online': True,
                          'network_data': [{'brd': '192.168.0.255',
                                            'ip': node_ip_management,
                                            'vlan': 103,
                                            'gateway': '192.168.0.1',
                                            'netmask': '255.255.255.0',
                                            'dev': 'eth0',
                                            'name': 'management'},
                                           {'brd': '240.0.1.255',
                                            'ip': node_ip_public,
                                            'vlan': 100,
                                            'gateway': '240.0.1.1',
                                            'netmask': '255.255.255.128',
                                            'dev': 'eth0',
                                            'name': u'public'},
                                           {'name': u'storage',
                                            'ip': node_ip_storage,
                                            'vlan': 102,
                                            'dev': 'eth0',
                                            'netmask': '255.255.255.0',
                                            'brd': '172.16.0.255',
                                            'gateway': u'172.16.0.1'},
                                           {'vlan': 100,
                                            'name': 'floating',
                                            'dev': 'eth0'},
                                           {'vlan': 101,
                                            'name': 'fixed',
                                            'dev': 'eth0'},
                                           {'name': u'admin',
                                            'dev': 'eth0'}]})

            pnd = {
                'profile': cluster_attrs['cobbler']['profile'],
                'power_type': 'ssh',
                'power_user': 'root',
                'power_address': n.ip,
                'power_pass': settings.PATH_TO_BOOTSTRAP_SSH_KEY,
                'name': TaskHelper.make_slave_name(n.id, n.role),
                'hostname': n.fqdn,
                'name_servers': '\"%s\"' % settings.DNS_SERVERS,
                'name_servers_search': '\"%s\"' % settings.DNS_SEARCH,
                'netboot_enabled': '1',
                'ks_meta': {
                    'puppet_auto_setup': 1,
                    'puppet_master': settings.PUPPET_MASTER_HOST,
                    'puppet_version': settings.PUPPET_VERSION,
                    'puppet_enable': 0,
                    'mco_auto_setup': 1,
                    'install_log_2_syslog': 1,
                    'mco_pskey': settings.MCO_PSKEY,
                    'mco_vhost': settings.MCO_VHOST,
                    'mco_host': settings.MCO_HOST,
                    'mco_user': settings.MCO_USER,
                    'mco_password': settings.MCO_PASSWORD,
                    'mco_connector': settings.MCO_CONNECTOR,
                    'mco_enable': 1,
                    'ks_spaces': "\"%s\"" % json.dumps(
                        n.attributes.volumes).replace("\"", "\\\""),
                    'auth_key': "\"%s\"" % cluster_attrs.get('auth_key', ''),
                }
            }

            netmanager = NetworkManager()
            netmanager.assign_admin_ips(
                n.id,
                len(n.meta.get('interfaces', []))
            )

            admin_ips = set([i.ip_addr for i in self.db.query(IPAddr).
                            filter_by(node=n.id).
                            filter_by(network=admin_net_id)])

            for i in n.meta.get('interfaces', []):
                if 'interfaces' not in pnd:
                    pnd['interfaces'] = {}
                pnd['interfaces'][i['name']] = {
                    'mac_address': i['mac'],
                    'static': '0',
                    'netmask': settings.ADMIN_NETWORK['netmask'],
                    'ip_address': admin_ips.pop(),
                }
                if 'interfaces_extra' not in pnd:
                    pnd['interfaces_extra'] = {}
                pnd['interfaces_extra'][i['name']] = {
                    'peerdns': 'no',
                    'onboot': 'no'
                }

                if i['mac'] == n.mac:
                    pnd['interfaces'][i['name']]['dns_name'] = n.fqdn
                    pnd['interfaces_extra'][i['name']]['onboot'] = 'yes'

            provision_nodes.append(pnd)

        controller_nodes = filter(
            lambda node: node['role'] == 'controller',
            nodes)
        msg['args']['attributes']['controller_nodes'] = controller_nodes
        msg['args']['nodes'] = nodes

        provision_task_uuid = [x.uuid for x in supertask.subtasks
                               if x.name == 'provision'][0]
        provision_msg = {
            'method': 'provision',
            'respond_to': 'provision_resp',
            'args': {
                'task_uuid': provision_task_uuid,
                'engine': {
                    'url': settings.COBBLER_URL,
                    'username': settings.COBBLER_USER,
                    'password': settings.COBBLER_PASSWORD,
                },
                'nodes': provision_nodes,
            }
        }

        nailgun.task.manager.rpc.cast.assert_called_once_with(
            'naily', [provision_msg, msg])

    @fake_tasks(fake_rpc=False, mock_rpc=False)
    @patch('nailgun.rpc.cast')
    def test_deploy_cast_with_vlan_manager(self, mocked_rpc):
        self.env.create(
            cluster_kwargs={
                'net_manager': 'VlanManager',
            },
            nodes_kwargs=[
                {"role": "controller", "pending_addition": True},
                {"role": "controller", "pending_addition": True},
            ]
        )

        deployment_task = self.env.launch_deployment()

        class VlanMatcher:
            def __init__(self):
                self.result = True

            def __eq__(self, *args, **kwargs):
                # Deploy message
                message = args[0][1]
                self.is_eql(
                    message['args']['attributes']['network_manager'],
                    'VlanManager')
                self.is_eql(
                    message['args']['attributes']['network_size'], 256)
                self.is_eql(
                    message['args']['attributes']['num_networks'], 1)
                self.is_eql(
                    message['args']['attributes']['vlan_start'], 101)

                for node in message['args']['nodes']:
                    # Set vlan interface
                    self.is_eql(
                        node['vlan_interface'],
                        'eth0')

                    # Don't set fixed network
                    fix_networks = filter(
                        lambda net: net['name'] == 'fixed',
                        node['network_data'])
                    self.is_eql(fix_networks, [])

                return self.result

            def is_eql(self, first, second):
                if first != second:
                    self.result = False

        nailgun.task.manager.rpc.cast.assert_called_with(
            'naily', VlanMatcher())

    @fake_tasks(fake_rpc=False, mock_rpc=False)
    @patch('nailgun.rpc.cast')
    def test_deploy_and_remove_correct_nodes_and_statuses(self, mocked_rpc):
        self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {
                    "pending_addition": True,
                },
                {
                    "status": "error",
                    "pending_deletion": True
                }
            ]
        )
        self.env.launch_deployment()

        # launch_deployment kicks ClusterChangesHandler
        # which in turns launches DeploymentTaskManager
        # which runs DeletionTask, ProvisionTask and DeploymentTask.
        # DeletionTask is sent to one orchestrator worker and
        # ProvisionTask and DeploymentTask messages are sent to
        # another orchestrator worker.
        # That is why we expect here list of two sets of
        # arguments in mocked nailgun.rpc.cast
        # The first set of args is for deletion task and
        # the second one is for provisioning and deployment.

        # remove_nodes method call [0][0][1]
        n_rpc_remove = nailgun.task.task.rpc.cast. \
            call_args_list[0][0][1]['args']['nodes']
        self.assertEquals(len(n_rpc_remove), 1)
        self.assertEquals(n_rpc_remove[0]['uid'], self.env.nodes[1].id)

        # provision method call [1][0][1][0]
        n_rpc_provision = nailgun.task.manager.rpc.cast. \
            call_args_list[1][0][1][0]['args']['nodes']
        # Nodes will be appended in provision list if
        # they 'pending_deletion' = False and
        # 'status' in ('discover', 'provisioning') or
        # 'status' = 'error' and 'error_type' = 'provision'
        # So, only one node from our list will be appended to
        # provision list.
        self.assertEquals(len(n_rpc_provision), 1)
        self.assertEquals(
            n_rpc_provision[0]['name'],
            TaskHelper.make_slave_name(self.env.nodes[0].id,
                                       self.env.nodes[0].role)
        )

        # deploy method call [1][0][1][1]
        n_rpc_deploy = nailgun.task.manager.rpc.cast. \
            call_args_list[1][0][1][1]['args']['nodes']
        self.assertEquals(len(n_rpc_deploy), 1)
        self.assertEquals(n_rpc_deploy[0]['uid'], self.env.nodes[0].id)

    @fake_tasks(fake_rpc=False, mock_rpc=False)
    @patch('nailgun.rpc.cast')
    def test_deploy_reruns_after_network_changes(self, mocked_rpc):
        self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {"status": "ready"},
                {
                    "pending_deletion": True,
                    "status": "ready",
                    "role": "compute"
                },
            ]
        )

        # for clean experiment
        cluster_db = self.env.clusters[0]
        cluster_db.clear_pending_changes()
        cluster_db.add_pending_changes('networks')

        nodes = sorted(cluster_db.nodes, key=lambda n: n.id)
        self.assertEqual(nodes[0].needs_redeploy, True)

        self.env.launch_deployment()

    def test_occurs_error_not_enough_ip_addresses(self):
        self.env.create(
            cluster_kwargs={},
            nodes_kwargs=[
                {'pending_addition': True},
                {'pending_addition': True},
                {'pending_addition': True}])

        cluster = self.env.clusters[0]

        public_network = self.db.query(
            NetworkGroup).filter_by(name='public').first()

        net_data = {
            "networks": [{
                'id': public_network.id,
                'ip_ranges': [[
                    '240.0.1.2',
                    '240.0.1.3']]}]}

        resp = self.app.put(
            reverse(
                'NetworkConfigurationHandler',
                kwargs={'cluster_id': cluster.id}),
            json.dumps(net_data),
            headers=self.default_headers)

        task = self.env.launch_deployment()

        self.assertEquals(task.status, 'error')
        self.assertEquals(
            task.message,
            'Not enough IP addresses. Public network must have at least '
            '3 IP addresses for the current environment.')

    def test_occurs_error_not_enough_free_space(self):
        meta = self.env.default_metadata()
        meta['disks'] = [{
            "model": "TOSHIBA MK1002TS",
            "name": "sda",
            "disk": "sda",
            # 8GB
            "size": 8000000}]

        node = self.env.create_node(meta=meta)
        cluster = self.env.create_cluster(nodes=[node.id])
        task = self.env.launch_deployment()

        self.assertEquals(task.status, 'error')
        self.assertEquals(
            task.message,
            "Node '%s' has insufficient disk space for OS" %
            node.human_readable_name)

    def test_occurs_error_not_enough_controllers_for_multinode(self):
        self.env.create(
            cluster_kwargs={
                'mode': 'multinode'},
            nodes_kwargs=[
                {'role': 'compute', 'pending_addition': True}])

        task = self.env.launch_deployment()

        self.assertEquals(task.status, 'error')
        self.assertEquals(
            task.message,
            "Not enough controllers, multinode mode requires at least 1 "
            "controller")

    def test_occurs_error_not_enough_controllers_for_ha(self):
        self.env.create(
            cluster_kwargs={
                'mode': 'ha'},
            nodes_kwargs=[
                {'role': 'compute', 'pending_addition': True}])

        task = self.env.launch_deployment()

        self.assertEquals(task.status, 'error')
        self.assertEquals(
            task.message,
            "Not enough controllers, ha mode requires at least 3 controllers")
