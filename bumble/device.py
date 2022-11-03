# Copyright 2021-2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------
from enum import IntEnum
import json
import asyncio
import logging
from contextlib import asynccontextmanager, AsyncExitStack
from dataclasses import dataclass

from .hci import *
from .host import Host
from .gatt import *
from .gap import GenericAccessService
from .core import AdvertisingData, BT_CENTRAL_ROLE, BT_PERIPHERAL_ROLE
from .utils import AsyncRunner, CompositeEventEmitter, setup_event_forwarding, composite_listener
from . import gatt_client
from . import gatt_server
from . import smp
from . import sdp
from . import l2cap
from . import keys

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
DEVICE_DEFAULT_ADDRESS                        = '00:00:00:00:00:00'
DEVICE_DEFAULT_ADVERTISING_INTERVAL           = 1000  # ms
DEVICE_DEFAULT_ADVERTISING_DATA               = ''
DEVICE_DEFAULT_NAME                           = 'Bumble'
DEVICE_DEFAULT_INQUIRY_LENGTH                 = 8  # 10.24 seconds
DEVICE_DEFAULT_CLASS_OF_DEVICE                = 0
DEVICE_DEFAULT_SCAN_RESPONSE_DATA             = b''
DEVICE_DEFAULT_DATA_LENGTH                    = (27, 328, 27, 328)
DEVICE_DEFAULT_SCAN_INTERVAL                  = 60  # ms
DEVICE_DEFAULT_SCAN_WINDOW                    = 60  # ms
DEVICE_DEFAULT_CONNECT_TIMEOUT                = None  # No timeout
DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL          = 60  # ms
DEVICE_DEFAULT_CONNECT_SCAN_WINDOW            = 60  # ms
DEVICE_DEFAULT_CONNECTION_INTERVAL_MIN        = 15  # ms
DEVICE_DEFAULT_CONNECTION_INTERVAL_MAX        = 30  # ms
DEVICE_DEFAULT_CONNECTION_MAX_LATENCY         = 0
DEVICE_DEFAULT_CONNECTION_SUPERVISION_TIMEOUT = 720  # ms
DEVICE_DEFAULT_CONNECTION_MIN_CE_LENGTH       = 0   # ms
DEVICE_DEFAULT_CONNECTION_MAX_CE_LENGTH       = 0   # ms

DEVICE_MIN_SCAN_INTERVAL                      = 25
DEVICE_MAX_SCAN_INTERVAL                      = 10240
DEVICE_MIN_SCAN_WINDOW                        = 25
DEVICE_MAX_SCAN_WINDOW                        = 10240
DEVICE_MIN_LE_RSSI                            = -127
DEVICE_MAX_LE_RSSI                            = 20


# -----------------------------------------------------------------------------
# Classes
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
class Advertisement:
    TX_POWER_NOT_AVAILABLE = HCI_LE_Extended_Advertising_Report_Event.TX_POWER_INFORMATION_NOT_AVAILABLE
    RSSI_NOT_AVAILABLE     = HCI_LE_Extended_Advertising_Report_Event.RSSI_NOT_AVAILABLE

    @classmethod
    def from_advertising_report(cls, report):
        if isinstance(report, HCI_LE_Advertising_Report_Event.Report):
            return LegacyAdvertisement.from_advertising_report(report)
        elif isinstance(report, HCI_LE_Extended_Advertising_Report_Event.Report):
            return ExtendedAdvertisement.from_advertising_report(report)

    def __init__(
        self,
        address,
        rssi             = HCI_LE_Extended_Advertising_Report_Event.RSSI_NOT_AVAILABLE,
        is_legacy        = False,
        is_anonymous     = False,
        is_connectable   = False,
        is_directed      = False,
        is_scannable     = False,
        is_scan_response = False,
        is_complete      = True,
        is_truncated     = False,
        primary_phy      = 0,
        secondary_phy    = 0,
        tx_power         = HCI_LE_Extended_Advertising_Report_Event.TX_POWER_INFORMATION_NOT_AVAILABLE,
        sid              = 0,
        data             = b''
    ):
        self.address          = address
        self.rssi             = rssi
        self.is_legacy        = is_legacy
        self.is_anonymous     = is_anonymous
        self.is_connectable   = is_connectable
        self.is_directed      = is_directed
        self.is_scannable     = is_scannable
        self.is_scan_response = is_scan_response
        self.is_complete      = is_complete
        self.is_truncated     = is_truncated
        self.primary_phy      = primary_phy
        self.secondary_phy    = secondary_phy
        self.tx_power         = tx_power
        self.sid              = sid
        self.data             = AdvertisingData.from_bytes(data)


# -----------------------------------------------------------------------------
class LegacyAdvertisement(Advertisement):
    @classmethod
    def from_advertising_report(cls, report):
        return cls(
            address          = report.address,
            rssi             = report.rssi,
            is_legacy        = True,
            is_connectable   = report.event_type in {
                HCI_LE_Advertising_Report_Event.ADV_IND,
                HCI_LE_Advertising_Report_Event.ADV_DIRECT_IND
            },
            is_directed      = report.event_type == HCI_LE_Advertising_Report_Event.ADV_DIRECT_IND,
            is_scannable     = report.event_type in {
                HCI_LE_Advertising_Report_Event.ADV_IND,
                HCI_LE_Advertising_Report_Event.ADV_SCAN_IND
            },
            is_scan_response = report.event_type == HCI_LE_Advertising_Report_Event.SCAN_RSP,
            data             = report.data
        )


# -----------------------------------------------------------------------------
class ExtendedAdvertisement(Advertisement):
    @classmethod
    def from_advertising_report(cls, report):
        return cls(
            address          = report.address,
            rssi             = report.rssi,
            is_legacy        = report.event_type & (1 << HCI_LE_Extended_Advertising_Report_Event.LEGACY_ADVERTISING_PDU_USED) != 0,
            is_anonymous     = report.address.address_type == HCI_LE_Extended_Advertising_Report_Event.ANONYMOUS_ADDRESS_TYPE,
            is_connectable   = report.event_type & (1 << HCI_LE_Extended_Advertising_Report_Event.CONNECTABLE_ADVERTISING) != 0,
            is_directed      = report.event_type & (1 << HCI_LE_Extended_Advertising_Report_Event.DIRECTED_ADVERTISING) != 0,
            is_scannable     = report.event_type & (1 << HCI_LE_Extended_Advertising_Report_Event.SCANNABLE_ADVERTISING) != 0,
            is_scan_response = report.event_type & (1 << HCI_LE_Extended_Advertising_Report_Event.SCAN_RESPONSE) != 0,
            is_complete      = (report.event_type >> 5 & 3)  == HCI_LE_Extended_Advertising_Report_Event.DATA_COMPLETE,
            is_truncated     = (report.event_type >> 5 & 3)  == HCI_LE_Extended_Advertising_Report_Event.DATA_INCOMPLETE_TRUNCATED_NO_MORE_TO_COME,
            primary_phy      = report.primary_phy,
            secondary_phy    = report.secondary_phy,
            tx_power         = report.tx_power,
            sid              = report.advertising_sid,
            data             = report.data
        )


# -----------------------------------------------------------------------------
class AdvertisementDataAccumulator:
    def __init__(self, passive=False):
        self.passive            = passive
        self.last_advertisement = None
        self.last_data          = b''

    def update(self, report):
        advertisement = Advertisement.from_advertising_report(report)
        result = None

        if advertisement.is_scan_response:
            if self.last_advertisement is not None and not self.last_advertisement.is_scan_response:
                # This is the response to a scannable advertisement
                result                = Advertisement.from_advertising_report(report)
                result.is_connectable = self.last_advertisement.is_connectable
                result.is_scannable   = True
                result.data           = AdvertisingData.from_bytes(self.last_data + report.data)
            self.last_data = b''
        else:
            if (
                self.passive or
                (not advertisement.is_scannable) or
                (self.last_advertisement is not None and not self.last_advertisement.is_scan_response)
            ):
                # Don't wait for a scan response
                result = Advertisement.from_advertising_report(report)

            self.last_data = report.data

        self.last_advertisement = advertisement

        return result


# -----------------------------------------------------------------------------
class AdvertisingType(IntEnum):
    UNDIRECTED_CONNECTABLE_SCANNABLE = 0x00  # Undirected, connectable,     scannable
    DIRECTED_CONNECTABLE_HIGH_DUTY   = 0x01  # Directed,   connectable,     non-scannable
    UNDIRECTED_SCANNABLE             = 0x02  # Undirected, non-connectable, scannable
    UNDIRECTED                       = 0x03  # Undirected, non-connectable, non-scannable
    DIRECTED_CONNECTABLE_LOW_DUTY    = 0x04  # Directed,   connectable,     non-scannable

    @property
    def has_data(self):
        return self in {
            AdvertisingType.UNDIRECTED_CONNECTABLE_SCANNABLE,
            AdvertisingType.UNDIRECTED_SCANNABLE,
            AdvertisingType.UNDIRECTED
        }

    @property
    def is_connectable(self):
        return self in {
            AdvertisingType.UNDIRECTED_CONNECTABLE_SCANNABLE,
            AdvertisingType.DIRECTED_CONNECTABLE_HIGH_DUTY,
            AdvertisingType.DIRECTED_CONNECTABLE_LOW_DUTY
        }

    @property
    def is_scannable(self):
        return self in {
            AdvertisingType.UNDIRECTED_CONNECTABLE_SCANNABLE,
            AdvertisingType.UNDIRECTED_SCANNABLE
        }

    @property
    def is_directed(self):
        return self in {
            AdvertisingType.DIRECTED_CONNECTABLE_HIGH_DUTY,
            AdvertisingType.DIRECTED_CONNECTABLE_LOW_DUTY
        }


# -----------------------------------------------------------------------------
class LePhyOptions:
    # Coded PHY preference
    ANY_CODED_PHY        = 0
    PREFER_S_2_CODED_PHY = 1
    PREFER_S_8_CODED_PHY = 2

    def __init__(self, coded_phy_preference=0):
        self.coded_phy_preference = coded_phy_preference

    def __int__(self):
        return self.coded_phy_preference & 3


# -----------------------------------------------------------------------------
class Peer:
    def __init__(self, connection):
        self.connection = connection

        # Create a GATT client for the connection
        self.gatt_client = gatt_client.Client(connection)
        connection.gatt_client = self.gatt_client

    @property
    def services(self):
        return self.gatt_client.services

    async def request_mtu(self, mtu):
        mtu = await self.gatt_client.request_mtu(mtu)
        self.connection.emit('connection_att_mtu_update')
        return mtu

    async def discover_service(self, uuid):
        return await self.gatt_client.discover_service(uuid)

    async def discover_services(self, uuids = []):
        return await self.gatt_client.discover_services(uuids)

    async def discover_included_services(self, service):
        return await self.gatt_client.discover_included_services(service)

    async def discover_characteristics(self, uuids = [], service = None):
        return await self.gatt_client.discover_characteristics(uuids = uuids, service = service)

    async def discover_descriptors(self, characteristic = None, start_handle = None, end_handle = None):
        return await self.gatt_client.discover_descriptors(characteristic, start_handle, end_handle)

    async def discover_attributes(self):
        return await self.gatt_client.discover_attributes()

    async def subscribe(self, characteristic, subscriber=None):
        return await self.gatt_client.subscribe(characteristic, subscriber)

    async def unsubscribe(self, characteristic, subscriber=None):
        return await self.gatt_client.unsubscribe(characteristic, subscriber)

    async def read_value(self, attribute):
        return await self.gatt_client.read_value(attribute)

    async def write_value(self, attribute, value, with_response=False):
        return await self.gatt_client.write_value(attribute, value, with_response)

    async def read_characteristics_by_uuid(self, uuid, service=None):
        return await self.gatt_client.read_characteristics_by_uuid(uuid, service)

    def get_services_by_uuid(self, uuid):
        return self.gatt_client.get_services_by_uuid(uuid)

    def get_characteristics_by_uuid(self, uuid, service = None):
        return self.gatt_client.get_characteristics_by_uuid(uuid, service)

    def create_service_proxy(self, proxy_class):
        return proxy_class.from_client(self.gatt_client)

    async def discover_service_and_create_proxy(self, proxy_class):
        # Discover the first matching service and its characteristics
        services = await self.discover_service(proxy_class.SERVICE_CLASS.UUID)
        if services:
            service = services[0]
            await service.discover_characteristics()
            return self.create_service_proxy(proxy_class)

    async def sustain(self, timeout=None):
        await self.connection.sustain(timeout)

    # [Classic only]
    async def request_name(self):
        return await self.connection.request_remote_name()

    async def __aenter__(self):
        await self.discover_services()
        for service in self.services:
            await self.discover_characteristics()

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        pass

    def __str__(self):
        return f'{self.connection.peer_address} as {self.connection.role_name}'


# -----------------------------------------------------------------------------
@dataclass
class ConnectionParametersPreferences:
    connection_interval_min: int = DEVICE_DEFAULT_CONNECTION_INTERVAL_MIN
    connection_interval_max: int = DEVICE_DEFAULT_CONNECTION_INTERVAL_MAX
    max_latency:             int = DEVICE_DEFAULT_CONNECTION_MAX_LATENCY
    supervision_timeout:     int = DEVICE_DEFAULT_CONNECTION_SUPERVISION_TIMEOUT
    min_ce_length:           int = DEVICE_DEFAULT_CONNECTION_MIN_CE_LENGTH
    max_ce_length:           int = DEVICE_DEFAULT_CONNECTION_MAX_CE_LENGTH


ConnectionParametersPreferences.default = ConnectionParametersPreferences()


# -----------------------------------------------------------------------------
class Connection(CompositeEventEmitter):
    @composite_listener
    class Listener:
        def on_disconnection(self, reason):
            pass

        def on_connection_parameters_update(self):
            pass

        def on_connection_parameters_update_failure(self, error):
            pass

        def on_connection_phy_update(self):
            pass

        def on_connection_phy_update_failure(self, error):
            pass

        def on_connection_att_mtu_update(self):
            pass

        def on_connection_encryption_change(self):
            pass

        def on_connection_encryption_key_refresh(self):
            pass

    def __init__(
        self,
        device,
        handle,
        transport,
        peer_address,
        peer_resolvable_address,
        role,
        parameters,
        phy
    ):
        super().__init__()
        self.device                  = device
        self.handle                  = handle
        self.transport               = transport
        self.peer_address            = peer_address
        self.peer_resolvable_address = peer_resolvable_address
        self.peer_name               = None  # Classic only
        self.role                    = role
        self.parameters              = parameters
        self.encryption              = 0
        self.authenticated           = False
        self.phy                     = phy
        self.att_mtu                 = ATT_DEFAULT_MTU
        self.data_length             = DEVICE_DEFAULT_DATA_LENGTH
        self.gatt_client             = None  # Per-connection client
        self.gatt_server             = device.gatt_server  # By default, use the device's shared server

    @property
    def role_name(self):
        return 'CENTRAL' if self.role == BT_CENTRAL_ROLE else 'PERIPHERAL'

    @property
    def is_encrypted(self):
        return self.encryption != 0

    def send_l2cap_pdu(self, cid, pdu):
        self.device.send_l2cap_pdu(self.handle, cid, pdu)

    def create_l2cap_connector(self, psm):
        return self.device.create_l2cap_connector(self, psm)

    async def disconnect(self, reason = HCI_REMOTE_USER_TERMINATED_CONNECTION_ERROR):
        return await self.device.disconnect(self, reason)

    async def pair(self):
        return await self.device.pair(self)

    def request_pairing(self):
        return self.device.request_pairing(self)

    # [Classic only]
    async def authenticate(self):
        return await self.device.authenticate(self)

    async def encrypt(self):
        return await self.device.encrypt(self)

    async def sustain(self, timeout=None):
        """ Idles the current task waiting for a disconnect or timeout """

        abort = asyncio.get_running_loop().create_future()
        self.on('disconnection', abort.set_result)
        self.on('disconnection_failure', abort.set_exception)

        try:
            await asyncio.wait_for(abort, timeout)
        except asyncio.TimeoutError:
            pass

        self.remove_listener('disconnection', abort.set_result)
        self.remove_listener('disconnection_failure', abort.set_exception)

    async def update_parameters(
        self,
        connection_interval_min,
        connection_interval_max,
        max_latency,
        supervision_timeout
    ):
        return await self.device.update_connection_parameters(
            self,
            connection_interval_min,
            connection_interval_max,
            max_latency,
            supervision_timeout
        )

    async def set_phy(self, tx_phys=None, rx_phys=None, phy_options=None):
        return await self.device.set_connection_phy(self, tx_phys, rx_phys, phy_options)

    async def get_rssi(self):
        return await self.device.get_connection_rssi(self)

    async def get_phy(self):
        return await self.device.get_connection_phy(self)

    # [Classic only]
    async def request_remote_name(self):
        return await self.device.request_remote_name(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            try:
                await self.disconnect()
            except HCI_StatusError as e:
                # Invalid parameter means the connection is no longer valid
                if e.error_code != HCI_INVALID_HCI_COMMAND_PARAMETERS_ERROR:
                    raise

    def __str__(self):
        return f'Connection(handle=0x{self.handle:04X}, role={self.role_name}, address={self.peer_address})'


# -----------------------------------------------------------------------------
class DeviceConfiguration:
    def __init__(self):
        # Setup defaults
        self.name                     = DEVICE_DEFAULT_NAME
        self.address                  = DEVICE_DEFAULT_ADDRESS
        self.class_of_device          = DEVICE_DEFAULT_CLASS_OF_DEVICE
        self.scan_response_data       = DEVICE_DEFAULT_SCAN_RESPONSE_DATA
        self.advertising_interval_min = DEVICE_DEFAULT_ADVERTISING_INTERVAL
        self.advertising_interval_max = DEVICE_DEFAULT_ADVERTISING_INTERVAL
        self.le_enabled               = True
        # LE host enable 2nd parameter
        self.le_simultaneous_enabled  = True
        self.classic_sc_enabled       = True
        self.classic_ssp_enabled      = True
        self.connectable              = True
        self.discoverable             = True
        self.advertising_data = bytes(
            AdvertisingData([(AdvertisingData.COMPLETE_LOCAL_NAME, bytes(self.name, 'utf-8'))])
        )
        self.irk      = bytes(16)  # This really must be changed for any level of security
        self.keystore = None

    def load_from_dict(self, config):
        # Load simple properties
        self.name = config.get('name', self.name)
        self.address = Address(config.get('address', self.address))
        self.class_of_device = config.get('class_of_device', self.class_of_device)
        self.advertising_interval_min = config.get('advertising_interval', self.advertising_interval_min)
        self.advertising_interval_max = self.advertising_interval_min
        self.keystore                 = config.get('keystore')
        self.le_enabled               = config.get('le_enabled', self.le_enabled)
        self.le_simultaneous_enabled  = config.get('le_simultaneous_enabled', self.le_simultaneous_enabled)
        self.classic_sc_enabled       = config.get('classic_sc_enabled', self.classic_sc_enabled)
        self.classic_ssp_enabled      = config.get('classic_ssp_enabled', self.classic_ssp_enabled)
        self.connectable              = config.get('connectable', self.connectable)
        self.discoverable             = config.get('discoverable', self.discoverable)

        # Load or synthesize an IRK
        irk = config.get('irk')
        if irk:
            self.irk = bytes.fromhex(irk)
        else:
            # Construct an IRK from the address bytes
            # NOTE: this is not secure, but will always give the same IRK for the same address
            address_bytes = bytes(self.address)
            self.irk = (address_bytes * 3)[:16]

        # Load advertising data
        advertising_data = config.get('advertising_data')
        if advertising_data:
            self.advertising_data = bytes.fromhex(advertising_data)

    def load_from_file(self, filename):
        with open(filename, 'r') as file:
            self.load_from_dict(json.load(file))

# -----------------------------------------------------------------------------
# Decorators used with the following Device class
# (we define them outside of the Device class, because defining decorators
#  within a class requires unnecessarily complicated acrobatics)
# -----------------------------------------------------------------------------


# Decorator that converts the first argument from a connection handle to a connection
def with_connection_from_handle(function):
    @functools.wraps(function)
    def wrapper(self, connection_handle, *args, **kwargs):
        if (connection := self.lookup_connection(connection_handle)) is None:
            raise ValueError('no connection for handle')
        return function(self, connection, *args, **kwargs)
    return wrapper


# Decorator that converts the first argument from a bluetooth address to a connection
def with_connection_from_address(function):
    @functools.wraps(function)
    def wrapper(self, address, *args, **kwargs):
        for connection in self.connections.values():
            if connection.peer_address == address:
                return function(self, connection, *args, **kwargs)
        raise ValueError('no connection for address')
    return wrapper


# Decorator that adds a method to the list of event handlers for host events.
# This assumes that the method name starts with `on_`
def host_event_handler(function):
    device_host_event_handlers.append(function.__name__[3:])
    return function


# List of host event handlers for the Device class.
# (we define this list outside the class, because referencing a class in method
#  decorators is not straightforward)
device_host_event_handlers = []


# -----------------------------------------------------------------------------
class Device(CompositeEventEmitter):

    @composite_listener
    class Listener:
        def on_advertisement(self, advertisement):
            pass

        def on_inquiry_result(self, address, class_of_device, data, rssi):
            pass

        def on_connection(self, connection):
            pass

        def on_connection_failure(self, error):
            pass

        def on_characteristic_subscription(self, connection, characteristic, notify_enabled, indicate_enabled):
            pass

    @classmethod
    def with_hci(cls, name, address, hci_source, hci_sink):
        '''
        Create a Device instance with a Host configured to communicate with a controller
        through an HCI source/sink
        '''
        host = Host(controller_source = hci_source, controller_sink = hci_sink)
        return cls(name = name, address = address, host = host)

    @classmethod
    def from_config_file(cls, filename):
        config = DeviceConfiguration()
        config.load_from_file(filename)
        return cls(config=config)

    @classmethod
    def from_config_file_with_hci(cls, filename, hci_source, hci_sink):
        config = DeviceConfiguration()
        config.load_from_file(filename)
        host = Host(controller_source = hci_source, controller_sink = hci_sink)
        return cls(config = config, host = host)

    def __init__(self, name = None, address = None, config = None, host = None, generic_access_service = True):
        super().__init__()

        self._host                      = None
        self.powered_on                 = False
        self.advertising                = False
        self.advertising_type           = None
        self.auto_restart_advertising   = False
        self.command_timeout            = 10  # seconds
        self.gatt_server                = gatt_server.Server(self)
        self.sdp_server                 = sdp.Server(self)
        self.l2cap_channel_manager      = l2cap.ChannelManager(
            [l2cap.L2CAP_Information_Request.EXTENDED_FEATURE_FIXED_CHANNELS]
        )
        self.advertisement_accumulators = {}  # Accumulators, by address
        self.scanning                   = False
        self.scanning_is_passive        = False
        self.discovering                = False
        self.connecting                 = False
        self.disconnecting              = False
        self.connections                = {}  # Connections, by connection handle
        self.classic_enabled            = False
        self.inquiry_response           = None
        self.address_resolver           = None

        # Use the initial config or a default
        self.public_address = Address('00:00:00:00:00:00')
        if config is None:
            config = DeviceConfiguration()
        self.name                     = config.name
        self.random_address           = config.address
        self.class_of_device          = config.class_of_device
        self.scan_response_data       = config.scan_response_data
        self.advertising_data         = config.advertising_data
        self.advertising_interval_min = config.advertising_interval_min
        self.advertising_interval_max = config.advertising_interval_max
        self.keystore                 = keys.KeyStore.create_for_device(config)
        self.irk                      = config.irk
        self.le_enabled               = config.le_enabled
        self.le_simultaneous_enabled  = config.le_simultaneous_enabled
        self.classic_ssp_enabled      = config.classic_ssp_enabled
        self.classic_sc_enabled       = config.classic_sc_enabled
        self.discoverable             = config.discoverable
        self.connectable              = config.connectable

        # If a name is passed, override the name from the config
        if name:
            self.name = name

        # If an address is passed, override the address from the config
        if address:
            if type(address) is str:
                address = Address(address)
            self.random_address = address

        # Setup SMP
        # TODO: allow using a public address
        self.smp_manager = smp.Manager(self, self.random_address)
        self.l2cap_channel_manager.register_fixed_channel(
            smp.SMP_CID, self.on_smp_pdu)
        self.l2cap_channel_manager.register_fixed_channel(
            smp.SMP_BR_CID, self.on_smp_pdu)

        # Register the SDP server with the L2CAP Channel Manager
        self.sdp_server.register(self.l2cap_channel_manager)

        # Add a GAP Service if requested
        if generic_access_service:
            self.gatt_server.add_service(GenericAccessService(self.name))
        self.l2cap_channel_manager.register_fixed_channel(ATT_CID, self.on_gatt_pdu)

        # Forward some events
        setup_event_forwarding(self.gatt_server, self, 'characteristic_subscription')

        # Set the initial host
        self.host = host

    @property
    def host(self):
        return self._host

    @host.setter
    def host(self, host):
        # Unsubscribe from events from the current host
        if self._host:
            for event_name in device_host_event_handlers:
                self._host.remove_listener(event_name, getattr(self, f'on_{event_name}'))

        # Subscribe to events from the new host
        if host:
            for event_name in device_host_event_handlers:
                host.on(event_name, getattr(self, f'on_{event_name}'))

        # Update the references to the new host
        self._host                      = host
        self.l2cap_channel_manager.host = host

        # Set providers for the new host
        if host:
            host.long_term_key_provider = self.get_long_term_key
            host.link_key_provider      = self.get_link_key

    @property
    def sdp_service_records(self):
        return self.sdp_server.service_records

    @sdp_service_records.setter
    def sdp_service_records(self, service_records):
        self.sdp_server.service_records = service_records

    def lookup_connection(self, connection_handle):
        if connection := self.connections.get(connection_handle):
            return connection

    def find_connection_by_bd_addr(self, bd_addr, transport=None):
        for connection in self.connections.values():
            if connection.peer_address.get_bytes() == bd_addr.get_bytes():
                if transport is None or connection.transport == transport:
                    return connection

    def register_l2cap_server(self, psm, server):
        self.l2cap_channel_manager.register_server(psm, server)

    def create_l2cap_connector(self, connection, psm):
        return lambda: self.l2cap_channel_manager.connect(connection, psm)

    def create_l2cap_registrar(self, psm):
        return lambda handler: self.register_l2cap_server(psm, handler)

    def send_l2cap_pdu(self, connection_handle, cid, pdu):
        self.host.send_l2cap_pdu(connection_handle, cid, pdu)

    async def send_command(self, command, check_result=False):
        try:
            return await asyncio.wait_for(
                self.host.send_command(command, check_result),
                self.command_timeout
            )
        except asyncio.TimeoutError:
            logger.warning('!!! Command timed out')
            raise CommandTimeoutError()

    async def power_on(self):
        # Reset the controller
        await self.host.reset()

        response = await self.send_command(HCI_Read_BD_ADDR_Command())
        if response.return_parameters.status == HCI_SUCCESS:
            logger.debug(color(f'BD_ADDR: {response.return_parameters.bd_addr}', 'yellow'))
            self.public_address = response.return_parameters.bd_addr

        if self.host.supports_command(HCI_WRITE_LE_HOST_SUPPORT_COMMAND):
            await self.send_command(HCI_Write_LE_Host_Support_Command(
                le_supported_host    = int(self.le_enabled),
                simultaneous_le_host = int(self.le_simultaneous_enabled),
            ))

        if self.le_enabled:
            # Set the controller address
            await self.send_command(HCI_LE_Set_Random_Address_Command(
                random_address = self.random_address
            ), check_result=True)

            # Load the address resolving list
            if self.keystore and self.host.supports_command(HCI_LE_CLEAR_RESOLVING_LIST_COMMAND):
                await self.send_command(HCI_LE_Clear_Resolving_List_Command())

                resolving_keys = await self.keystore.get_resolving_keys()
                for (irk, address) in resolving_keys:
                    await self.send_command(
                        HCI_LE_Add_Device_To_Resolving_List_Command(
                            peer_identity_address_type = address.address_type,
                            peer_identity_address      = address,
                            peer_irk                   = irk,
                            local_irk                  = self.irk
                        )
                    )

                # Enable address resolution
                # await self.send_command(
                #     HCI_LE_Set_Address_Resolution_Enable_Command(address_resolution_enable=1)
                # )

                # Create a host-side address resolver
                self.address_resolver = smp.AddressResolver(resolving_keys)

        if self.classic_enabled:
            await self.send_command(
                HCI_Write_Local_Name_Command(local_name=self.name.encode('utf8'))
            )
            await self.send_command(
                HCI_Write_Class_Of_Device_Command(class_of_device = self.class_of_device)
            )
            await self.send_command(
                HCI_Write_Simple_Pairing_Mode_Command(
                    simple_pairing_mode=int(self.classic_ssp_enabled))
            )
            await self.send_command(
                HCI_Write_Secure_Connections_Host_Support_Command(
                    secure_connections_host_support=int(self.classic_sc_enabled))
            )
            await self.set_connectable(self.connectable)
            await self.set_discoverable(self.discoverable)

        # Let the SMP manager know about the address
        # TODO: allow using a public address
        self.smp_manager.address = self.random_address

        # Done
        self.powered_on = True

    def supports_le_feature(self, feature):
        return self.host.supports_le_feature(feature)

    def supports_le_phy(self, phy):
        if phy == HCI_LE_1M_PHY:
            return True

        feature_map = {
            HCI_LE_2M_PHY:    HCI_LE_2M_PHY_LE_SUPPORTED_FEATURE,
            HCI_LE_CODED_PHY: HCI_LE_CODED_PHY_LE_SUPPORTED_FEATURE
        }
        if phy not in feature_map:
            raise ValueError('invalid PHY')

        return self.host.supports_le_feature(feature_map[phy])

    async def start_advertising(
        self,
        advertising_type=AdvertisingType.UNDIRECTED_CONNECTABLE_SCANNABLE,
        target=None,
        auto_restart=False
    ):
        # If we're advertising, stop first
        if self.advertising:
            await self.stop_advertising()

        # Set/update the advertising data if the advertising type allows it
        if advertising_type.has_data:
            await self.send_command(HCI_LE_Set_Advertising_Data_Command(
                advertising_data = self.advertising_data
            ), check_result=True)

        # Set/update the scan response data if the advertising is scannable
        if advertising_type.is_scannable:
            await self.send_command(HCI_LE_Set_Scan_Response_Data_Command(
                scan_response_data = self.scan_response_data
            ), check_result=True)

        # Decide what peer address to use
        if advertising_type.is_directed:
            if target is None:
                raise ValueError('directed advertising requires a target address')

            peer_address      = target
            peer_address_type = target.address_type
        else:
            peer_address      = Address('00:00:00:00:00:00')
            peer_address_type = Address.PUBLIC_DEVICE_ADDRESS

        # Set the advertising parameters
        await self.send_command(HCI_LE_Set_Advertising_Parameters_Command(
            advertising_interval_min  = self.advertising_interval_min,
            advertising_interval_max  = self.advertising_interval_max,
            advertising_type          = int(advertising_type),
            own_address_type          = Address.RANDOM_DEVICE_ADDRESS,  # TODO: allow using the public address
            peer_address_type         = peer_address_type,
            peer_address              = peer_address,
            advertising_channel_map   = 7,
            advertising_filter_policy = 0
        ), check_result=True)

        # Enable advertising
        await self.send_command(HCI_LE_Set_Advertising_Enable_Command(
            advertising_enable = 1
        ), check_result=True)

        self.auto_restart_advertising = auto_restart
        self.advertising_type         = advertising_type
        self.advertising              = True

    async def stop_advertising(self):
        # Disable advertising
        if self.advertising:
            await self.send_command(HCI_LE_Set_Advertising_Enable_Command(
                advertising_enable = 0
            ), check_result=True)

            self.advertising              = False
            self.advertising_type         = None
            self.auto_restart_advertising = False

    @property
    def is_advertising(self):
        return self.advertising

    async def start_scanning(
        self,
        active=True,
        scan_interval=DEVICE_DEFAULT_SCAN_INTERVAL,  # Scan interval in ms
        scan_window=DEVICE_DEFAULT_SCAN_WINDOW,      # Scan window in ms
        own_address_type=Address.RANDOM_DEVICE_ADDRESS,
        filter_duplicates=False,
        scanning_phys=(HCI_LE_1M_PHY, HCI_LE_CODED_PHY)
    ):
        # Check that the arguments are legal
        if scan_interval < scan_window:
            raise ValueError('scan_interval must be >= scan_window')
        if scan_interval < DEVICE_MIN_SCAN_INTERVAL or scan_interval > DEVICE_MAX_SCAN_INTERVAL:
            raise ValueError('scan_interval out of range')
        if scan_window < DEVICE_MIN_SCAN_WINDOW or scan_window > DEVICE_MAX_SCAN_WINDOW:
            raise ValueError('scan_interval out of range')

        # Reset the accumulators
        self.advertisement_accumulator = {}

        # Enable scanning
        if self.supports_le_feature(HCI_LE_EXTENDED_ADVERTISING_LE_SUPPORTED_FEATURE):
            # Set the scanning parameters
            scan_type = HCI_LE_Set_Extended_Scan_Parameters_Command.ACTIVE_SCANNING if active else HCI_LE_Set_Extended_Scan_Parameters_Command.PASSIVE_SCANNING
            scanning_filter_policy = HCI_LE_Set_Extended_Scan_Parameters_Command.BASIC_UNFILTERED_POLICY  # TODO: support other types

            scanning_phy_count = 0
            scanning_phys_bits = 0
            if HCI_LE_1M_PHY in scanning_phys:
                scanning_phys_bits |= 1 << HCI_LE_1M_PHY_BIT
                scanning_phy_count += 1
            if HCI_LE_CODED_PHY in scanning_phys:
                if self.supports_le_feature(HCI_LE_CODED_PHY_LE_SUPPORTED_FEATURE):
                    scanning_phys_bits |= 1 << HCI_LE_CODED_PHY_BIT
                    scanning_phy_count += 1

            if scanning_phy_count == 0:
                raise ValueError('at least one scanning PHY must be enabled')

            await self.send_command(HCI_LE_Set_Extended_Scan_Parameters_Command(
                own_address_type       = own_address_type,
                scanning_filter_policy = scanning_filter_policy,
                scanning_phys          = scanning_phys_bits,
                scan_types             = [scan_type] * scanning_phy_count,
                scan_intervals         = [int(scan_window / 0.625)] * scanning_phy_count,
                scan_windows           = [int(scan_window / 0.625)] * scanning_phy_count
            ), check_result=True)

            # Enable scanning
            await self.send_command(HCI_LE_Set_Extended_Scan_Enable_Command(
                enable            = 1,
                filter_duplicates = 1 if filter_duplicates else 0,
                duration          = 0,  # TODO allow other values
                period            = 0   # TODO allow other values
            ), check_result=True)
        else:
            # Set the scanning parameters
            scan_type = HCI_LE_Set_Scan_Parameters_Command.ACTIVE_SCANNING if active else HCI_LE_Set_Scan_Parameters_Command.PASSIVE_SCANNING
            await self.send_command(HCI_LE_Set_Scan_Parameters_Command(
                le_scan_type           = scan_type,
                le_scan_interval       = int(scan_window / 0.625),
                le_scan_window         = int(scan_window / 0.625),
                own_address_type       = own_address_type,
                scanning_filter_policy = HCI_LE_Set_Scan_Parameters_Command.BASIC_UNFILTERED_POLICY
            ), check_result=True)

            # Enable scanning
            await self.send_command(HCI_LE_Set_Scan_Enable_Command(
                le_scan_enable    = 1,
                filter_duplicates = 1 if filter_duplicates else 0
            ), check_result=True)

        self.scanning_is_passive = not active
        self.scanning            = True

    async def stop_scanning(self):
        # Disable scanning
        if self.supports_le_feature(HCI_LE_EXTENDED_ADVERTISING_LE_SUPPORTED_FEATURE):
            await self.send_command(HCI_LE_Set_Extended_Scan_Enable_Command(
                enable            = 0,
                filter_duplicates = 0,
                duration          = 0,
                period            = 0
            ), check_result=True)
        else:
            await self.send_command(HCI_LE_Set_Scan_Enable_Command(
                le_scan_enable    = 0,
                filter_duplicates = 0
            ), check_result=True)

        self.scanning = False

    @property
    def is_scanning(self):
        return self.scanning

    @host_event_handler
    def on_advertising_report(self, report):
        if not (accumulator := self.advertisement_accumulators.get(report.address)):
            accumulator = AdvertisementDataAccumulator(passive=self.scanning_is_passive)
            self.advertisement_accumulators[report.address] = accumulator
        if advertisement := accumulator.update(report):
            self.emit('advertisement', advertisement)

    async def start_discovery(self):
        await self.send_command(HCI_Write_Inquiry_Mode_Command(
            inquiry_mode=HCI_EXTENDED_INQUIRY_MODE
        ), check_result=True)

        response = await self.send_command(HCI_Inquiry_Command(
            lap            = HCI_GENERAL_INQUIRY_LAP,
            inquiry_length = DEVICE_DEFAULT_INQUIRY_LENGTH,
            num_responses  = 0  # Unlimited number of responses.
        ))
        if response.status != HCI_Command_Status_Event.PENDING:
            self.discovering = False
            raise HCI_StatusError(response)

        self.discovering = True

    async def stop_discovery(self):
        await self.send_command(HCI_Inquiry_Cancel_Command(), check_result=True)
        self.discovering = False

    @host_event_handler
    def on_inquiry_result(self, address, class_of_device, data, rssi):
        self.emit(
            'inquiry_result',
            address,
            class_of_device,
            AdvertisingData.from_bytes(data),
            rssi
        )

    async def set_scan_enable(self, inquiry_scan_enabled, page_scan_enabled):
        if inquiry_scan_enabled and page_scan_enabled:
            scan_enable = 0x03
        elif page_scan_enabled:
            scan_enable = 0x02
        elif inquiry_scan_enabled:
            scan_enable = 0x01
        else:
            scan_enable = 0x00

        return await self.send_command(HCI_Write_Scan_Enable_Command(scan_enable = scan_enable))

    async def set_discoverable(self, discoverable=True):
        self.discoverable = discoverable
        if self.classic_enabled:
            # Synthesize an inquiry response if none is set already
            if self.inquiry_response is None:
                self.inquiry_response = bytes(
                    AdvertisingData([
                        (AdvertisingData.COMPLETE_LOCAL_NAME, bytes(self.name, 'utf-8'))
                    ])
                )

            # Update the controller
            await self.send_command(
                HCI_Write_Extended_Inquiry_Response_Command(
                    fec_required              = 0,
                    extended_inquiry_response = self.inquiry_response
                ),
                check_result=True
            )
            await self.set_scan_enable(
                inquiry_scan_enabled = self.discoverable,
                page_scan_enabled    = self.connectable
            )

    async def set_connectable(self, connectable=True):
        self.connectable = connectable
        if self.classic_enabled:
            await self.set_scan_enable(
                inquiry_scan_enabled = self.discoverable,
                page_scan_enabled    = self.connectable
            )

    async def connect(
        self,
        peer_address,
        transport=BT_LE_TRANSPORT,
        connection_parameters_preferences=None,
        timeout=DEVICE_DEFAULT_CONNECT_TIMEOUT
    ):
        '''
        Request a connection to a peer.
        This method cannot be called if there is already a pending connection.

        connection_parameters_preferences: (BLE only, ignored for BR/EDR)
          * None: use all PHYs with default parameters
          * map: each entry has a PHY as key and a ConnectionParametersPreferences object as value
        '''

        # Check parameters
        if transport not in {BT_LE_TRANSPORT, BT_BR_EDR_TRANSPORT}:
            raise ValueError('invalid transport')

        # Adjust the transport automatically if we need to
        if transport == BT_LE_TRANSPORT and not self.le_enabled:
            transport = BT_BR_EDR_TRANSPORT
        elif transport == BT_BR_EDR_TRANSPORT and not self.classic_enabled:
            transport = BT_LE_TRANSPORT

        # Check that there isn't already a pending connection
        if transport == BT_LE_TRANSPORT and self.is_connecting:
            raise InvalidStateError('connection already pending')

        if type(peer_address) is str:
            try:
                peer_address = Address(peer_address)
            except ValueError:
                # If the address is not parsable, assume it is a name instead
                logger.debug('looking for peer by name')
                peer_address = await self.find_peer_by_name(peer_address, transport)  # TODO: timeout

        def on_connection(connection):
            if transport == BT_LE_TRANSPORT or (
                # match BR/EDR connection event against peer address
                connection.transport == transport and connection.peer_address == peer_address):
                pending_connection.set_result(connection)

        def on_connection_failure(error):
            if transport == BT_LE_TRANSPORT or (
                # match BR/EDR connection failure event against peer address
                error.transport == transport and error.peer_address == peer_address):
                pending_connection.set_exception(error)

        # Create a future so that we can wait for the connection's result
        pending_connection = asyncio.get_running_loop().create_future()
        self.on('connection', on_connection)
        self.on('connection_failure', on_connection_failure)

        try:
            # Tell the controller to connect
            if transport == BT_LE_TRANSPORT:
                if connection_parameters_preferences is None:
                    if connection_parameters_preferences is None:
                        connection_parameters_preferences = {
                            HCI_LE_1M_PHY:    ConnectionParametersPreferences.default,
                            HCI_LE_2M_PHY:    ConnectionParametersPreferences.default,
                            HCI_LE_CODED_PHY: ConnectionParametersPreferences.default
                        }

                if self.host.supports_command(HCI_LE_EXTENDED_CREATE_CONNECTION_COMMAND):
                    # Only keep supported PHYs
                    phys = sorted(list(set(filter(self.supports_le_phy, connection_parameters_preferences.keys()))))
                    if not phys:
                        raise ValueError('least one supported PHY needed')

                    phy_count = len(phys)
                    initiating_phys = phy_list_to_bits(phys)

                    connection_interval_mins = [
                        int(connection_parameters_preferences[phy].connection_interval_min / 1.25) for phy in phys
                    ]
                    connection_interval_maxs = [
                        int(connection_parameters_preferences[phy].connection_interval_max / 1.25) for phy in phys
                    ]
                    max_latencies = [
                        connection_parameters_preferences[phy].max_latency for phy in phys
                    ]
                    supervision_timeouts = [
                        int(connection_parameters_preferences[phy].supervision_timeout / 10) for phy in phys
                    ]
                    min_ce_lengths = [
                        int(connection_parameters_preferences[phy].min_ce_length / 0.625) for phy in phys
                    ]
                    max_ce_lengths = [
                        int(connection_parameters_preferences[phy].max_ce_length / 0.625) for phy in phys
                    ]

                    result = await self.send_command(HCI_LE_Extended_Create_Connection_Command(
                        initiator_filter_policy  = 0,
                        own_address_type         = Address.RANDOM_DEVICE_ADDRESS,
                        peer_address_type        = peer_address.address_type,
                        peer_address             = peer_address,
                        initiating_phys          = initiating_phys,
                        scan_intervals           = (int(DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL / 0.625),) * phy_count,
                        scan_windows             = (int(DEVICE_DEFAULT_CONNECT_SCAN_WINDOW / 0.625),) * phy_count,
                        connection_interval_mins = connection_interval_mins,
                        connection_interval_maxs = connection_interval_maxs,
                        max_latencies            = max_latencies,
                        supervision_timeouts     = supervision_timeouts,
                        min_ce_lengths           = min_ce_lengths,
                        max_ce_lengths           = max_ce_lengths
                    ))
                else:
                    if HCI_LE_1M_PHY not in connection_parameters_preferences:
                        raise ValueError('1M PHY preferences required')

                    prefs = connection_parameters_preferences[HCI_LE_1M_PHY]
                    result = await self.send_command(HCI_LE_Create_Connection_Command(
                        le_scan_interval        = int(DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL / 0.625),
                        le_scan_window          = int(DEVICE_DEFAULT_CONNECT_SCAN_WINDOW / 0.625),
                        initiator_filter_policy = 0,
                        peer_address_type       = peer_address.address_type,
                        peer_address            = peer_address,
                        own_address_type        = Address.RANDOM_DEVICE_ADDRESS,
                        connection_interval_min = int(prefs.connection_interval_min / 1.25),
                        connection_interval_max = int(prefs.connection_interval_max / 1.25),
                        max_latency             = prefs.max_latency,
                        supervision_timeout     = int(prefs.supervision_timeout / 10),
                        min_ce_length           = int(prefs.min_ce_length / 0.625),
                        max_ce_length           = int(prefs.max_ce_length / 0.625),
                    ))
            else:
                # TODO: allow passing other settings
                result = await self.send_command(HCI_Create_Connection_Command(
                    bd_addr                   = peer_address,
                    packet_type               = 0xCC18,  # FIXME: change
                    page_scan_repetition_mode = HCI_R2_PAGE_SCAN_REPETITION_MODE,
                    clock_offset              = 0x0000,
                    allow_role_switch         = 0x01,
                    reserved                  = 0
                ))

            if result.status != HCI_Command_Status_Event.PENDING:
                raise HCI_StatusError(result)

            # Wait for the connection process to complete
            if transport == BT_LE_TRANSPORT:
                self.connecting = True
            if timeout is None:
                return await pending_connection
            else:
                try:
                    return await asyncio.wait_for(asyncio.shield(pending_connection), timeout)
                except asyncio.TimeoutError:
                    if transport == BT_LE_TRANSPORT:
                        await self.send_command(HCI_LE_Create_Connection_Cancel_Command())
                    else:
                        await self.send_command(HCI_Create_Connection_Cancel_Command(bd_addr=peer_address))

                    try:
                        return await pending_connection
                    except ConnectionError:
                        raise TimeoutError()
        finally:
            self.remove_listener('connection', on_connection)
            self.remove_listener('connection_failure', on_connection_failure)
            if transport == BT_LE_TRANSPORT:
                self.connecting = False

    @asynccontextmanager
    async def connect_as_gatt(self, peer_address):
        async with AsyncExitStack() as stack:
            connection = await stack.enter_async_context(await self.connect(peer_address))
            peer = await stack.enter_async_context(Peer(connection))

            yield peer

    @property
    def is_connecting(self):
        return self.connecting

    @property
    def is_disconnecting(self):
        return self.disconnecting

    async def cancel_connection(self):
        if not self.is_connecting:
            return
        await self.send_command(HCI_LE_Create_Connection_Cancel_Command(), check_result=True)

    async def disconnect(self, connection, reason):
        # Create a future so that we can wait for the disconnection's result
        pending_disconnection = asyncio.get_running_loop().create_future()
        connection.on('disconnection', pending_disconnection.set_result)
        connection.on('disconnection_failure', pending_disconnection.set_exception)

        # Request a disconnection
        result = await self.send_command(HCI_Disconnect_Command(
            connection_handle = connection.handle, reason = reason
        ))

        try:
            if result.status != HCI_Command_Status_Event.PENDING:
                raise HCI_StatusError(result)

            # Wait for the disconnection process to complete
            self.disconnecting = True
            return await pending_disconnection
        finally:
            connection.remove_listener('disconnection', pending_disconnection.set_result)
            connection.remove_listener('disconnection_failure', pending_disconnection.set_exception)
            self.disconnecting = False

    async def update_connection_parameters(
        self,
        connection,
        connection_interval_min,
        connection_interval_max,
        max_latency,
        supervision_timeout,
        min_ce_length = 0,
        max_ce_length = 0
    ):
        '''
        NOTE: the name of the parameters may look odd, but it just follows the names used in the Bluetooth spec.
        '''
        await self.send_command(HCI_LE_Connection_Update_Command(
            connection_handle       = connection.handle,
            connection_interval_min = connection_interval_min,
            connection_interval_max = connection_interval_max,
            max_latency             = max_latency,
            supervision_timeout     = supervision_timeout,
            min_ce_length           = min_ce_length,
            max_ce_length           = max_ce_length
        ), check_result=True)

    async def get_connection_rssi(self, connection):
        result = await self.send_command(HCI_Read_RSSI_Command(handle = connection.handle), check_result=True)
        return result.return_parameters.rssi

    async def get_connection_phy(self, connection):
        result = await self.send_command(
            HCI_LE_Read_PHY_Command(connection_handle = connection.handle),
            check_result=True
        )
        return (result.return_parameters.tx_phy, result.return_parameters.rx_phy)

    async def set_connection_phy(
        self,
        connection,
        tx_phys=None,
        rx_phys=None,
        phy_options=None
    ):
        all_phys_bits = (1 if tx_phys is None else 0) | ((1 if rx_phys is None else 0) << 1)

        return await self.send_command(
            HCI_LE_Set_PHY_Command(
                connection_handle = connection.handle,
                all_phys          = all_phys_bits,
                tx_phys           = phy_list_to_bits(tx_phys),
                rx_phys           = phy_list_to_bits(rx_phys),
                phy_options       = 0 if phy_options is None else int(phy_options)
            ), check_result=True
        )

    async def set_default_phy(self, tx_phys=None, rx_phys=None):
        all_phys_bits = (1 if tx_phys is None else 0) | ((1 if rx_phys is None else 0) << 1)

        return await self.send_command(
            HCI_LE_Set_Default_PHY_Command(
                all_phys = all_phys_bits,
                tx_phys  = phy_list_to_bits(tx_phys),
                rx_phys  = phy_list_to_bits(rx_phys)
            ), check_result=True
        )

    async def find_peer_by_name(self, name, transport=BT_LE_TRANSPORT):
        """
        Scan for a peer with a give name and return its address and transport
        """

        # Create a future to wait for an address to be found
        peer_address = asyncio.get_running_loop().create_future()

        # Scan/inquire with event handlers to handle scan/inquiry results
        def on_peer_found(address, ad_data):
            local_name = ad_data.get(AdvertisingData.COMPLETE_LOCAL_NAME)
            if local_name is None:
                local_name = ad_data.get(AdvertisingData.SHORTENED_LOCAL_NAME)
            if local_name is not None:
                if local_name.decode('utf-8') == name:
                    peer_address.set_result(address)
        try:
            handler = None
            if transport == BT_LE_TRANSPORT:
                event_name = 'advertisement'
                handler = self.on(
                    event_name,
                    lambda advertisement: on_peer_found(advertisement.address, advertisement.data)
                )

                was_scanning = self.scanning
                if not self.scanning:
                    await self.start_scanning(filter_duplicates=True)

            elif transport == BT_BR_EDR_TRANSPORT:
                event_name = 'inquiry_result'
                handler = self.on(
                    event_name,
                    lambda address, class_of_device, eir_data, rssi:
                        on_peer_found(address, eir_data)
                )

                was_discovering = self.discovering
                if not self.discovering:
                    await self.start_discovery()
            else:
                return None

            return await peer_address
        finally:
            if handler is not None:
                self.remove_listener(event_name, handler)

            if transport == BT_LE_TRANSPORT and not was_scanning:
                await self.stop_scanning()
            elif transport == BT_BR_EDR_TRANSPORT and not was_discovering:
                await self.stop_discovery()

    @property
    def pairing_config_factory(self):
        return self.smp_manager.pairing_config_factory

    @pairing_config_factory.setter
    def pairing_config_factory(self, pairing_config_factory):
        self.smp_manager.pairing_config_factory = pairing_config_factory

    async def pair(self, connection):
        return await self.smp_manager.pair(connection)

    def request_pairing(self, connection):
        return self.smp_manager.request_pairing(connection)

    async def get_long_term_key(self, connection_handle, rand, ediv):
        if (connection := self.lookup_connection(connection_handle)) is None:
            return

        # Start by looking for the key in an SMP session
        ltk = self.smp_manager.get_long_term_key(connection, rand, ediv)
        if ltk is not None:
            return ltk

        # Then look for the key in the keystore
        if self.keystore is not None:
            keys = await self.keystore.get(str(connection.peer_address))
            if keys is not None:
                logger.debug('found keys in the key store')
                if keys.ltk:
                    return keys.ltk.value
                elif connection.role == BT_CENTRAL_ROLE and keys.ltk_central:
                    return keys.ltk_central.value
                elif connection.role == BT_PERIPHERAL_ROLE and keys.ltk_peripheral:
                    return keys.ltk_peripheral.value

    async def get_link_key(self, address):
        # Look for the key in the keystore
        if self.keystore is not None:
            keys = await self.keystore.get(str(address))
            if keys is not None:
                logger.debug('found keys in the key store')
                return keys.link_key.value

    # [Classic only]
    async def authenticate(self, connection):
        # Set up event handlers
        pending_authentication = asyncio.get_running_loop().create_future()

        def on_authentication():
            pending_authentication.set_result(None)

        def on_authentication_failure(error_code):
            pending_authentication.set_exception(HCI_Error(error_code))

        connection.on('connection_authentication', on_authentication)
        connection.on('connection_authentication_failure',  on_authentication_failure)

        # Request the authentication
        try:
            result = await self.send_command(
                HCI_Authentication_Requested_Command(connection_handle = connection.handle)
            )
            if result.status != HCI_COMMAND_STATUS_PENDING:
                logger.warn(f'HCI_Authentication_Requested_Command failed: {HCI_Constant.error_name(result.status)}')
                raise HCI_StatusError(result)

            # Wait for the authentication to complete
            await pending_authentication
        finally:
            connection.remove_listener('connection_authentication', on_authentication)
            connection.remove_listener('connection_authentication_failure',  on_authentication_failure)

    async def encrypt(self, connection):
        # Set up event handlers
        pending_encryption = asyncio.get_running_loop().create_future()

        def on_encryption_change():
            pending_encryption.set_result(None)

        def on_encryption_failure(error_code):
            pending_encryption.set_exception(HCI_Error(error_code))

        connection.on('connection_encryption_change',  on_encryption_change)
        connection.on('connection_encryption_failure', on_encryption_failure)

        # Request the encryption
        try:
            if connection.transport == BT_LE_TRANSPORT:
                # Look for a key in the key store
                if self.keystore is None:
                    raise RuntimeError('no key store')

                keys = await self.keystore.get(str(connection.peer_address))
                if keys is None:
                    raise RuntimeError('keys not found in key store')

                if keys.ltk is not None:
                    ltk  = keys.ltk.value
                    rand = bytes(8)
                    ediv = 0
                elif keys.ltk_central is not None:
                    ltk  = keys.ltk_central.value
                    rand = keys.ltk_central.rand
                    ediv = keys.ltk_central.ediv
                else:
                    raise RuntimeError('no LTK found for peer')

                if connection.role != HCI_CENTRAL_ROLE:
                    raise InvalidStateError('only centrals can start encryption')

                result = await self.send_command(
                    HCI_LE_Enable_Encryption_Command(
                        connection_handle     = connection.handle,
                        random_number         = rand,
                        encrypted_diversifier = ediv,
                        long_term_key         = ltk
                    )
                )

                if result.status != HCI_COMMAND_STATUS_PENDING:
                    logger.warn(f'HCI_LE_Enable_Encryption_Command failed: {HCI_Constant.error_name(result.status)}')
                    raise HCI_StatusError(result)
            else:
                result = await self.send_command(
                    HCI_Set_Connection_Encryption_Command(
                        connection_handle = connection.handle,
                        encryption_enable = 0x01
                    )
                )

                if result.status != HCI_COMMAND_STATUS_PENDING:
                    logger.warn(f'HCI_Set_Connection_Encryption_Command failed: {HCI_Constant.error_name(result.status)}')
                    raise HCI_StatusError(result)

            # Wait for the result
            await pending_encryption
        finally:
            connection.remove_listener('connection_encryption_change',  on_encryption_change)
            connection.remove_listener('connection_encryption_failure', on_encryption_failure)

    # [Classic only]
    async def request_remote_name(self, connection):
        # Set up event handlers
        pending_name = asyncio.get_running_loop().create_future()

        def on_remote_name():
            pending_name.set_result(connection.peer_name)

        def on_remote_name_failure(error_code):
            pending_name.set_exception(HCI_Error(error_code))

        connection.on('remote_name', on_remote_name)
        connection.on('remote_name_failure', on_remote_name_failure)

        try:
            result = await self.send_command(
                HCI_Remote_Name_Request_Command(
                    bd_addr                   = connection.peer_address,
                    page_scan_repetition_mode = HCI_Remote_Name_Request_Command.R0,  # TODO investigate other options
                    reserved                  = 0,
                    clock_offset              = 0  # TODO investigate non-0 values
                )
            )

            if result.status != HCI_COMMAND_STATUS_PENDING:
                logger.warn(f'HCI_Set_Connection_Encryption_Command failed: {HCI_Constant.error_name(result.status)}')
                raise HCI_StatusError(result)

            # Wait for the result
            return await pending_name
        finally:
            connection.remove_listener('remote_name', on_remote_name)
            connection.remove_listener('remote_name_failure', on_remote_name_failure)

    # [Classic only]
    @host_event_handler
    def on_link_key(self, bd_addr, link_key, key_type):
        # Store the keys in the key store
        if self.keystore:
            pairing_keys = keys.PairingKeys()
            pairing_keys.link_key = keys.PairingKeys.Key(value = link_key)

            async def store_keys():
                try:
                    await self.keystore.update(str(bd_addr), pairing_keys)
                except Exception as error:
                    logger.warn(f'!!! error while storing keys: {error}')

            asyncio.create_task(store_keys())

    def add_service(self, service):
        self.gatt_server.add_service(service)

    def add_services(self, services):
        self.gatt_server.add_services(services)

    async def notify_subscriber(self, connection, attribute, value=None, force=False):
        await self.gatt_server.notify_subscriber(connection, attribute, value, force)

    async def notify_subscribers(self, attribute, value=None, force=False):
        await self.gatt_server.notify_subscribers(attribute, value, force)

    async def indicate_subscriber(self, connection, attribute, value=None, force=False):
        await self.gatt_server.indicate_subscriber(connection, attribute, value, force)

    async def indicate_subscribers(self, attribute, value=None, force=False):
        await self.gatt_server.indicate_subscribers(attribute, value, force)

    @host_event_handler
    def on_connection(self, connection_handle, transport, peer_address, peer_resolvable_address, role, connection_parameters):
        logger.debug(f'*** Connection: [0x{connection_handle:04X}] {peer_address} as {HCI_Constant.role_name(role)}')
        if connection_handle in self.connections:
            logger.warn('new connection reuses the same handle as a previous connection')

        if transport == BT_BR_EDR_TRANSPORT:
            # Create a new connection
            connection = Connection(
                self,
                connection_handle,
                transport,
                peer_address,
                peer_resolvable_address,
                role,
                connection_parameters,
                phy=None
            )
            self.connections[connection_handle] = connection

            # Emit an event to notify listeners of the new connection
            self.emit('connection', connection)
        else:
            # Resolve the peer address if we can
            if self.address_resolver:
                if peer_address.is_resolvable:
                    resolved_address = self.address_resolver.resolve(peer_address)
                    if resolved_address is not None:
                        logger.debug(f'*** Address resolved as {resolved_address}')
                        peer_resolvable_address = peer_address
                        peer_address = resolved_address

            # We are no longer advertising
            self.advertising = False

            # Create and notify of the new connection asynchronously
            async def new_connection():
                # Figure out which PHY we're connected with
                if self.host.supports_command(HCI_LE_READ_PHY_COMMAND):
                    result = await self.send_command(
                        HCI_LE_Read_PHY_Command(connection_handle=connection_handle),
                        check_result=True
                    )
                    phy = ConnectionPHY(result.return_parameters.tx_phy, result.return_parameters.rx_phy)
                else:
                    phy = ConnectionPHY(HCI_LE_1M_PHY, HCI_LE_1M_PHY)

                # Create a new connection
                connection = Connection(
                    self,
                    connection_handle,
                    transport,
                    peer_address,
                    peer_resolvable_address,
                    role,
                    connection_parameters,
                    phy
                )
                self.connections[connection_handle] = connection

                # Emit an event to notify listeners of the new connection
                self.emit('connection', connection)

            asyncio.create_task(new_connection())

    @host_event_handler
    def on_connection_failure(self, transport, peer_address, error_code):
        logger.debug(f'*** Connection failed: {HCI_Constant.error_name(error_code)}')

        # For directed advertising, this means a timeout
        if transport == BT_LE_TRANSPORT and self.advertising and self.advertising_type.is_directed:
            self.advertising = False

        # Notify listeners
        error = ConnectionError(
            error_code,
            transport,
            peer_address,
            'hci',
            HCI_Constant.error_name(error_code)
        )
        self.emit('connection_failure', error)

    @host_event_handler
    @with_connection_from_handle
    def on_disconnection(self, connection, reason):
        logger.debug(f'*** Disconnection: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, reason={reason}')
        connection.emit('disconnection', reason)

        # Remove the connection from the map
        del self.connections[connection.handle]

        # Cleanup subsystems that maintain per-connection state
        self.gatt_server.on_disconnection(connection)

        # Restart advertising if auto-restart is enabled
        if self.auto_restart_advertising:
            logger.debug('restarting advertising')
            asyncio.create_task(self.start_advertising(
                advertising_type = self.advertising_type,
                auto_restart     = True
            ))

    @host_event_handler
    @with_connection_from_handle
    def on_disconnection_failure(self, connection, error_code):
        logger.debug(f'*** Disconnection failed: {error_code}')
        error = ConnectionError(
            error_code,
            connection.transport,
            connection.peer_address,
            'hci',
            HCI_Constant.error_name(error_code)
        )
        connection.emit('disconnection_failure', error)

    @host_event_handler
    @AsyncRunner.run_in_task()
    async def on_inquiry_complete(self):
        if self.discovering:
            # Inquire again
            await self.start_discovery()

    @host_event_handler
    @with_connection_from_handle
    def on_connection_authentication(self, connection):
        logger.debug(f'*** Connection Authentication: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}')
        connection.authenticated = True
        connection.emit('connection_authentication')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_authentication_failure(self, connection, error):
        logger.debug(f'*** Connection Authentication Failure: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, error={error}')
        connection.emit('connection_authentication_failure', error)

    # [Classic only]
    @host_event_handler
    @with_connection_from_address
    def on_authentication_io_capability_request(self, connection):
        # Ask what the pairing config should be for this connection
        pairing_config = self.pairing_config_factory(connection)

        # Map the SMP IO capability to a Classic IO capability
        io_capability = {
            smp.SMP_DISPLAY_ONLY_IO_CAPABILITY:       HCI_DISPLAY_ONLY_IO_CAPABILITY,
            smp.SMP_DISPLAY_YES_NO_IO_CAPABILITY:     HCI_DISPLAY_YES_NO_IO_CAPABILITY,
            smp.SMP_KEYBOARD_ONLY_IO_CAPABILITY:      HCI_KEYBOARD_ONLY_IO_CAPABILITY,
            smp.SMP_NO_INPUT_NO_OUTPUT_IO_CAPABILITY: HCI_NO_INPUT_NO_OUTPUT_IO_CAPABILITY,
            smp.SMP_KEYBOARD_DISPLAY_IO_CAPABILITY:   HCI_DISPLAY_YES_NO_IO_CAPABILITY
        }.get(pairing_config.delegate.io_capability)

        if io_capability is None:
            logger.warning(f'cannot map IO capability ({pairing_config.delegate.io_capability}')
            io_capability = HCI_NO_INPUT_NO_OUTPUT_IO_CAPABILITY

        # Compute the authentication requirements
        authentication_requirements = (
            # No Bonding
            (
                HCI_MITM_NOT_REQUIRED_NO_BONDING_AUTHENTICATION_REQUIREMENTS,
                HCI_MITM_REQUIRED_NO_BONDING_AUTHENTICATION_REQUIREMENTS
            ),
            # General Bonding
            (
                HCI_MITM_NOT_REQUIRED_GENERAL_BONDING_AUTHENTICATION_REQUIREMENTS,
                HCI_MITM_REQUIRED_GENERAL_BONDING_AUTHENTICATION_REQUIREMENTS
            )
        )[1 if pairing_config.bonding else 0][1 if pairing_config.mitm else 0]

        # Respond
        self.host.send_command_sync(
            HCI_IO_Capability_Request_Reply_Command(
                bd_addr                     = connection.peer_address,
                io_capability               = io_capability,
                oob_data_present            = 0x00,  # Not present
                authentication_requirements = authentication_requirements
            )
        )

    # [Classic only]
    @host_event_handler
    @with_connection_from_address
    def on_authentication_user_confirmation_request(self, connection, code):
        # Ask what the pairing config should be for this connection
        pairing_config = self.pairing_config_factory(connection)

        can_confirm = pairing_config.delegate.io_capability not in {
            smp.SMP_NO_INPUT_NO_OUTPUT_IO_CAPABILITY,
            smp.SMP_DISPLAY_ONLY_IO_CAPABILITY
        }

        # Respond
        if can_confirm and pairing_config.delegate:
            async def compare_numbers():
                numbers_match = await pairing_config.delegate.compare_numbers(code, digits=6)
                if numbers_match:
                    self.host.send_command_sync(
                        HCI_User_Confirmation_Request_Reply_Command(bd_addr=connection.peer_address)
                    )
                else:
                    self.host.send_command_sync(
                        HCI_User_Confirmation_Request_Negative_Reply_Command(bd_addr=connection.peer_address)
                    )

            asyncio.create_task(compare_numbers())
        else:
            self.host.send_command_sync(
                HCI_User_Confirmation_Request_Reply_Command(bd_addr=connection.peer_address)
            )

    # [Classic only]
    @host_event_handler
    @with_connection_from_address
    def on_authentication_user_passkey_request(self, connection):
        # Ask what the pairing config should be for this connection
        pairing_config = self.pairing_config_factory(connection)

        can_input = pairing_config.delegate.io_capability in {
            smp.SMP_KEYBOARD_ONLY_IO_CAPABILITY,
            smp.SMP_KEYBOARD_DISPLAY_IO_CAPABILITY
        }

        # Respond
        if can_input and pairing_config.delegate:
            async def get_number():
                number = await pairing_config.delegate.get_number()
                if number is not None:
                    self.host.send_command_sync(
                        HCI_User_Passkey_Request_Reply_Command(
                            bd_addr       = connection.peer_address,
                            numeric_value = number)
                    )
                else:
                    self.host.send_command_sync(
                        HCI_User_Passkey_Request_Negative_Reply_Command(bd_addr=connection.peer_address)
                    )

            asyncio.create_task(get_number())
        else:
            self.host.send_command_sync(
                HCI_User_Passkey_Request_Negative_Reply_Command(bd_addr=connection.peer_address)
            )

    # [Classic only]
    @host_event_handler
    @with_connection_from_address
    def on_remote_name(self, connection, remote_name):
        # Try to decode the name
        try:
            connection.peer_name = remote_name.decode('utf-8')
            connection.emit('remote_name')
        except UnicodeDecodeError as error:
            logger.warning('peer name is not valid UTF-8')
            connection.emit('remote_name_failure', error)

    # [Classic only]
    @host_event_handler
    @with_connection_from_address
    def on_remote_name_failure(self, connection, error):
        connection.emit('remote_name_failure', error)

    @host_event_handler
    @with_connection_from_handle
    def on_connection_encryption_change(self, connection, encryption):
        logger.debug(f'*** Connection Encryption Change: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, encryption={encryption}')
        connection.encryption = encryption
        connection.emit('connection_encryption_change')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_encryption_failure(self, connection, error):
        logger.debug(f'*** Connection Encryption Failure: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, error={error}')
        connection.emit('connection_encryption_failure', error)

    @host_event_handler
    @with_connection_from_handle
    def on_connection_encryption_key_refresh(self, connection):
        logger.debug(f'*** Connection Key Refresh: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}')
        connection.emit('connection_encryption_key_refresh')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_parameters_update(self, connection, connection_parameters):
        logger.debug(f'*** Connection Parameters Update: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, {connection_parameters}')
        connection.parameters = connection_parameters
        connection.emit('connection_parameters_update')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_parameters_update_failure(self, connection, error):
        logger.debug(f'*** Connection Parameters Update Failed: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, error={error}')
        connection.emit('connection_parameters_update_failure', error)

    @host_event_handler
    @with_connection_from_handle
    def on_connection_phy_update(self, connection, connection_phy):
        logger.debug(f'*** Connection PHY Update: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, {connection_phy}')
        connection.phy = connection_phy
        connection.emit('connection_phy_update')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_phy_update_failure(self, connection, error):
        logger.debug(f'*** Connection PHY Update Failed: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, error={error}')
        connection.emit('connection_phy_update_failure', error)

    @host_event_handler
    @with_connection_from_handle
    def on_connection_att_mtu_update(self, connection, att_mtu):
        logger.debug(f'*** Connection ATT MTU Update: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}, {att_mtu}')
        connection.att_mtu = att_mtu
        connection.emit('connection_att_mtu_update')

    @host_event_handler
    @with_connection_from_handle
    def on_connection_data_length_change(self, connection, max_tx_octets, max_tx_time, max_rx_octets, max_rx_time):
        logger.debug(f'*** Connection Data Length Change: [0x{connection.handle:04X}] {connection.peer_address} as {connection.role_name}')
        connection.data_length = (max_tx_octets, max_tx_time, max_rx_octets, max_rx_time)
        connection.emit('connection_data_length_change')

    @host_event_handler
    @with_connection_from_address
    def on_pin_code_request(self, connection):
        pairing_config = self.pairing_config_factory(connection)

        async def get_pin_code():
            pin_code = await pairing_config.delegate.get_pin_code()
            if pin_code is not None:
                self.host.send_command_sync(
                    HCI_PIN_Code_Request_Reply_Command(
                        bd_addr         = connection.peer_address,
                        pin_code_length = len(pin_code),
                        pin_code        = pin_code.ljust(16, b'\0'),) # padding to 16 octets
                )
            else:
                self.host.send_command_sync(
                    HCI_PIN_Code_Request_Negative_Reply_Command(bd_addr=connection.peer_address)
                )
        asyncio.create_task(get_pin_code())

    @with_connection_from_handle
    def on_pairing_start(self, connection):
        connection.emit('pairing_start')

    @with_connection_from_handle
    def on_pairing(self, connection, keys):
        connection.emit('pairing', keys)

    @with_connection_from_handle
    def on_pairing_failure(self, connection, reason):
        connection.emit('pairing_failure', reason)

    @with_connection_from_handle
    def on_gatt_pdu(self, connection, pdu):
        # Parse the L2CAP payload into an ATT PDU object
        att_pdu = ATT_PDU.from_bytes(pdu)

        # Conveniently, even-numbered op codes are client->server and
        # odd-numbered ones are server->client
        if att_pdu.op_code & 1:
            if connection.gatt_client is None:
                logger.warn(color('no GATT client for connection 0x{connection_handle:04X}'))
                return
            connection.gatt_client.on_gatt_pdu(att_pdu)
        else:
            if connection.gatt_server is None:
                logger.warn(color('no GATT server for connection 0x{connection_handle:04X}'))
                return
            connection.gatt_server.on_gatt_pdu(connection, att_pdu)

    @with_connection_from_handle
    def on_smp_pdu(self, connection, pdu):
        self.smp_manager.on_smp_pdu(connection, pdu)

    @host_event_handler
    @with_connection_from_handle
    def on_l2cap_pdu(self, connection, cid, pdu):
        self.l2cap_channel_manager.on_pdu(connection, cid, pdu)

    def __str__(self):
        return f'Device(name="{self.name}", random_address="{self.random_address}"", public_address="{self.public_address}")'
