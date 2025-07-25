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
from __future__ import annotations
import collections
import dataclasses
from dataclasses import field
import enum
import functools
import logging
import secrets
import struct
from collections.abc import Sequence
from typing import Any, Callable, Iterable, Optional, Union, TypeVar, ClassVar, cast
from typing_extensions import Self

from bumble import crypto
from bumble.colors import color
from bumble.core import (
    DeviceClass,
    InvalidArgumentError,
    InvalidPacketError,
    PhysicalTransport,
    ProtocolError,
    bit_flags_to_strings,
    name_or_number,
    padded_bytes,
)
from bumble import utils


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def hci_command_op_code(ogf, ocf):
    return ogf << 10 | ocf


def hci_vendor_command_op_code(ocf):
    return hci_command_op_code(HCI_VENDOR_OGF, ocf)


def key_with_value(dictionary, target_value):
    for key, value in dictionary.items():
        if value == target_value:
            return key
    return None


def indent_lines(string):
    return '\n'.join(['  ' + line for line in string.split('\n')])


def map_null_terminated_utf8_string(utf8_bytes):
    try:
        terminator = utf8_bytes.find(0)
        if terminator < 0:
            terminator = len(utf8_bytes)
        return utf8_bytes[0:terminator].decode('utf8')
    except UnicodeDecodeError:
        return utf8_bytes


def map_class_of_device(class_of_device):
    (
        service_classes,
        major_device_class,
        minor_device_class,
    ) = DeviceClass.split_class_of_device(class_of_device)
    return (
        f'[{class_of_device:06X}] Services('
        f'{",".join(DeviceClass.service_class_labels(service_classes))}),'
        f'Class({DeviceClass.major_device_class_name(major_device_class)}|'
        f'{DeviceClass.minor_device_class_name(major_device_class, minor_device_class)}'
        ')'
    )


def phy_list_to_bits(phys: Optional[Iterable[Phy]]) -> int:
    if phys is None:
        return 0

    phy_bits = 0
    for phy in phys:
        if phy not in HCI_LE_PHY_TYPE_TO_BIT:
            raise InvalidArgumentError('invalid PHY')
        phy_bits |= 1 << HCI_LE_PHY_TYPE_TO_BIT[phy]
    return phy_bits


class SpecableEnum(utils.OpenIntEnum):

    @classmethod
    def type_spec(cls, size: int):
        return {'size': size, 'mapper': lambda x: cls(x).name}

    @classmethod
    def type_metadata(cls, size: int, list_begin: bool = False, list_end: bool = False):
        return metadata(cls.type_spec(size), list_begin=list_begin, list_end=list_end)


class SpecableFlag(enum.IntFlag):

    @classmethod
    def type_spec(cls, size: int):
        return {'size': size, 'mapper': lambda x: cls(x).name}

    @classmethod
    def type_metadata(cls, size: int, list_begin: bool = False, list_end: bool = False):
        return metadata(cls.type_spec(size), list_begin=list_begin, list_end=list_end)


# -----------------------------------------------------------------------------
# Field Metadata
# -----------------------------------------------------------------------------
# Field specification can be:
# - a dict with "serializer", "parser", "size", "mapper" keys
# - a callable that takes (packet, offset) and returns (new_offset, value) (deserialize only)
# - a string of
#   - ">2" and ">4" for 2-byte and 4-byte big-endian integers
#   - "*" for all remaining bytes in the packet
#   - "v" for variable length bytes with a leading length byte
# - an integer [1, 4] for 1-byte, 2-byte or 4-byte unsigned little-endian integers
# - an integer [-2, -1] for 1-byte, 2-byte signed little-endian integers
FieldSpec = Union[dict[str, Any], Callable[[bytes, int], tuple[int, Any]], str, int]
Fields = Sequence[Union[tuple[str, FieldSpec], 'Fields']]


@dataclasses.dataclass
class FieldMetadata:
    spec: FieldSpec
    list_begin: bool = False
    list_end: bool = False


def metadata(
    spec: FieldSpec, list_begin: bool = False, list_end: bool = False
) -> dict[str, Any]:
    return {
        "bumble.hci": FieldMetadata(spec=spec, list_begin=list_begin, list_end=list_end)
    }


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=line-too-long

HCI_VENDOR_OGF = 0x3F

# HCI Version
HCI_VERSION_BLUETOOTH_CORE_1_0B    = 0
HCI_VERSION_BLUETOOTH_CORE_1_1     = 1
HCI_VERSION_BLUETOOTH_CORE_1_2     = 2
HCI_VERSION_BLUETOOTH_CORE_2_0_EDR = 3
HCI_VERSION_BLUETOOTH_CORE_2_1_EDR = 4
HCI_VERSION_BLUETOOTH_CORE_3_0_HS  = 5
HCI_VERSION_BLUETOOTH_CORE_4_0     = 6
HCI_VERSION_BLUETOOTH_CORE_4_1     = 7
HCI_VERSION_BLUETOOTH_CORE_4_2     = 8
HCI_VERSION_BLUETOOTH_CORE_5_0     = 9
HCI_VERSION_BLUETOOTH_CORE_5_1     = 10
HCI_VERSION_BLUETOOTH_CORE_5_2     = 11
HCI_VERSION_BLUETOOTH_CORE_5_3     = 12
HCI_VERSION_BLUETOOTH_CORE_5_4     = 13
HCI_VERSION_BLUETOOTH_CORE_6_0     = 14

HCI_VERSION_NAMES = {
    HCI_VERSION_BLUETOOTH_CORE_1_0B:    'HCI_VERSION_BLUETOOTH_CORE_1_0B',
    HCI_VERSION_BLUETOOTH_CORE_1_1:     'HCI_VERSION_BLUETOOTH_CORE_1_1',
    HCI_VERSION_BLUETOOTH_CORE_1_2:     'HCI_VERSION_BLUETOOTH_CORE_1_2',
    HCI_VERSION_BLUETOOTH_CORE_2_0_EDR: 'HCI_VERSION_BLUETOOTH_CORE_2_0_EDR',
    HCI_VERSION_BLUETOOTH_CORE_2_1_EDR: 'HCI_VERSION_BLUETOOTH_CORE_2_1_EDR',
    HCI_VERSION_BLUETOOTH_CORE_3_0_HS:  'HCI_VERSION_BLUETOOTH_CORE_3_0_HS',
    HCI_VERSION_BLUETOOTH_CORE_4_0:     'HCI_VERSION_BLUETOOTH_CORE_4_0',
    HCI_VERSION_BLUETOOTH_CORE_4_1:     'HCI_VERSION_BLUETOOTH_CORE_4_1',
    HCI_VERSION_BLUETOOTH_CORE_4_2:     'HCI_VERSION_BLUETOOTH_CORE_4_2',
    HCI_VERSION_BLUETOOTH_CORE_5_0:     'HCI_VERSION_BLUETOOTH_CORE_5_0',
    HCI_VERSION_BLUETOOTH_CORE_5_1:     'HCI_VERSION_BLUETOOTH_CORE_5_1',
    HCI_VERSION_BLUETOOTH_CORE_5_2:     'HCI_VERSION_BLUETOOTH_CORE_5_2',
    HCI_VERSION_BLUETOOTH_CORE_5_3:     'HCI_VERSION_BLUETOOTH_CORE_5_3',
    HCI_VERSION_BLUETOOTH_CORE_5_4:     'HCI_VERSION_BLUETOOTH_CORE_5_4',
    HCI_VERSION_BLUETOOTH_CORE_6_0:     'HCI_VERSION_BLUETOOTH_CORE_6_0',
}

# LMP Version
LMP_VERSION_NAMES = HCI_VERSION_NAMES

# HCI Packet types
HCI_COMMAND_PACKET          = 0x01
HCI_ACL_DATA_PACKET         = 0x02
HCI_SYNCHRONOUS_DATA_PACKET = 0x03
HCI_EVENT_PACKET            = 0x04
HCI_ISO_DATA_PACKET         = 0x05

# HCI Event Codes
HCI_INQUIRY_COMPLETE_EVENT                                       = 0x01
HCI_INQUIRY_RESULT_EVENT                                         = 0x02
HCI_CONNECTION_COMPLETE_EVENT                                    = 0x03
HCI_CONNECTION_REQUEST_EVENT                                     = 0x04
HCI_DISCONNECTION_COMPLETE_EVENT                                 = 0x05
HCI_AUTHENTICATION_COMPLETE_EVENT                                = 0x06
HCI_REMOTE_NAME_REQUEST_COMPLETE_EVENT                           = 0x07
HCI_ENCRYPTION_CHANGE_EVENT                                      = 0x08
HCI_CHANGE_CONNECTION_LINK_KEY_COMPLETE_EVENT                    = 0x09
HCI_LINK_KEY_TYPE_CHANGED_EVENT                                  = 0x0A
HCI_READ_REMOTE_SUPPORTED_FEATURES_COMPLETE_EVENT                = 0x0B
HCI_READ_REMOTE_VERSION_INFORMATION_COMPLETE_EVENT               = 0x0C
HCI_QOS_SETUP_COMPLETE_EVENT                                     = 0x0D
HCI_COMMAND_COMPLETE_EVENT                                       = 0x0E
HCI_COMMAND_STATUS_EVENT                                         = 0x0F
HCI_HARDWARE_ERROR_EVENT                                         = 0x10
HCI_FLUSH_OCCURRED_EVENT                                         = 0x11
HCI_ROLE_CHANGE_EVENT                                            = 0x12
HCI_NUMBER_OF_COMPLETED_PACKETS_EVENT                            = 0x13
HCI_MODE_CHANGE_EVENT                                            = 0x14
HCI_RETURN_LINK_KEYS_EVENT                                       = 0x15
HCI_PIN_CODE_REQUEST_EVENT                                       = 0x16
HCI_LINK_KEY_REQUEST_EVENT                                       = 0x17
HCI_LINK_KEY_NOTIFICATION_EVENT                                  = 0x18
HCI_LOOPBACK_COMMAND_EVENT                                       = 0x19
HCI_DATA_BUFFER_OVERFLOW_EVENT                                   = 0x1A
HCI_MAX_SLOTS_CHANGE_EVENT                                       = 0x1B
HCI_READ_CLOCK_OFFSET_COMPLETE_EVENT                             = 0x1C
HCI_CONNECTION_PACKET_TYPE_CHANGED_EVENT                         = 0x1D
HCI_QOS_VIOLATION_EVENT                                          = 0x1E
HCI_PAGE_SCAN_REPETITION_MODE_CHANGE_EVENT                       = 0x20
HCI_FLOW_SPECIFICATION_COMPLETE_EVENT                            = 0x21
HCI_INQUIRY_RESULT_WITH_RSSI_EVENT                               = 0x22
HCI_READ_REMOTE_EXTENDED_FEATURES_COMPLETE_EVENT                 = 0x23
HCI_SYNCHRONOUS_CONNECTION_COMPLETE_EVENT                        = 0x2C
HCI_SYNCHRONOUS_CONNECTION_CHANGED_EVENT                         = 0x2D
HCI_SNIFF_SUBRATING_EVENT                                        = 0x2E
HCI_EXTENDED_INQUIRY_RESULT_EVENT                                = 0x2F
HCI_ENCRYPTION_KEY_REFRESH_COMPLETE_EVENT                        = 0x30
HCI_IO_CAPABILITY_REQUEST_EVENT                                  = 0x31
HCI_IO_CAPABILITY_RESPONSE_EVENT                                 = 0x32
HCI_USER_CONFIRMATION_REQUEST_EVENT                              = 0x33
HCI_USER_PASSKEY_REQUEST_EVENT                                   = 0x34
HCI_REMOTE_OOB_DATA_REQUEST_EVENT                                = 0x35
HCI_SIMPLE_PAIRING_COMPLETE_EVENT                                = 0x36
HCI_LINK_SUPERVISION_TIMEOUT_CHANGED_EVENT                       = 0x38
HCI_ENHANCED_FLUSH_COMPLETE_EVENT                                = 0x39
HCI_USER_PASSKEY_NOTIFICATION_EVENT                              = 0x3B
HCI_KEYPRESS_NOTIFICATION_EVENT                                  = 0x3C
HCI_REMOTE_HOST_SUPPORTED_FEATURES_NOTIFICATION_EVENT            = 0x3D
HCI_LE_META_EVENT                                                = 0x3E
HCI_NUMBER_OF_COMPLETED_DATA_BLOCKS_EVENT                        = 0x48
HCI_TRIGGERED_CLOCK_CAPTURE_EVENT                                = 0X4E
HCI_SYNCHRONIZATION_TRAIN_COMPLETE_EVENT                         = 0X4F
HCI_SYNCHRONIZATION_TRAIN_RECEIVED_EVENT                         = 0X50
HCI_CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVE_EVENT            = 0X51
HCI_CONNECTIONLESS_PERIPHERAL_BROADCAST_TIMEOUT_EVENT            = 0X52
HCI_TRUNCATED_PAGE_COMPLETE_EVENT                                = 0X53
HCI_PERIPHERAL_PAGE_RESPONSE_TIMEOUT_EVENT                       = 0X54
HCI_CONNECTIONLESS_PERIPHERAL_BROADCAST_CHANNEL_MAP_CHANGE_EVENT = 0X55
HCI_INQUIRY_RESPONSE_NOTIFICATION_EVENT                          = 0X56
HCI_AUTHENTICATED_PAYLOAD_TIMEOUT_EXPIRED_EVENT                  = 0X57
HCI_SAM_STATUS_CHANGE_EVENT                                      = 0X58
HCI_ENCRYPTION_CHANGE_V2_EVENT                                   = 0x59

HCI_VENDOR_EVENT = 0xFF


# HCI Subevent Codes
HCI_LE_CONNECTION_COMPLETE_EVENT                            = 0x01
HCI_LE_ADVERTISING_REPORT_EVENT                             = 0x02
HCI_LE_CONNECTION_UPDATE_COMPLETE_EVENT                     = 0x03
HCI_LE_READ_REMOTE_FEATURES_COMPLETE_EVENT                  = 0x04
HCI_LE_LONG_TERM_KEY_REQUEST_EVENT                          = 0x05
HCI_LE_REMOTE_CONNECTION_PARAMETER_REQUEST_EVENT            = 0x06
HCI_LE_DATA_LENGTH_CHANGE_EVENT                             = 0x07
HCI_LE_READ_LOCAL_P_256_PUBLIC_KEY_COMPLETE_EVENT           = 0x08
HCI_LE_GENERATE_DHKEY_COMPLETE_EVENT                        = 0x09
HCI_LE_ENHANCED_CONNECTION_COMPLETE_EVENT                   = 0x0A
HCI_LE_DIRECTED_ADVERTISING_REPORT_EVENT                    = 0x0B
HCI_LE_PHY_UPDATE_COMPLETE_EVENT                            = 0x0C
HCI_LE_EXTENDED_ADVERTISING_REPORT_EVENT                    = 0x0D
HCI_LE_PERIODIC_ADVERTISING_SYNC_ESTABLISHED_EVENT          = 0x0E
HCI_LE_PERIODIC_ADVERTISING_REPORT_EVENT                    = 0x0F
HCI_LE_PERIODIC_ADVERTISING_SYNC_LOST_EVENT                 = 0x10
HCI_LE_SCAN_TIMEOUT_EVENT                                   = 0x11
HCI_LE_ADVERTISING_SET_TERMINATED_EVENT                     = 0x12
HCI_LE_SCAN_REQUEST_RECEIVED_EVENT                          = 0x13
HCI_LE_CHANNEL_SELECTION_ALGORITHM_EVENT                    = 0x14
HCI_LE_CONNECTIONLESS_IQ_REPORT_EVENT                       = 0X15
HCI_LE_CONNECTION_IQ_REPORT_EVENT                           = 0X16
HCI_LE_CTE_REQUEST_FAILED_EVENT                             = 0X17
HCI_LE_PERIODIC_ADVERTISING_SYNC_TRANSFER_RECEIVED_EVENT    = 0X18
HCI_LE_CIS_ESTABLISHED_EVENT                                = 0X19
HCI_LE_CIS_REQUEST_EVENT                                    = 0X1A
HCI_LE_CREATE_BIG_COMPLETE_EVENT                            = 0X1B
HCI_LE_TERMINATE_BIG_COMPLETE_EVENT                         = 0X1C
HCI_LE_BIG_SYNC_ESTABLISHED_EVENT                           = 0X1D
HCI_LE_BIG_SYNC_LOST_EVENT                                  = 0X1E
HCI_LE_REQUEST_PEER_SCA_COMPLETE_EVENT                      = 0X1F
HCI_LE_PATH_LOSS_THRESHOLD_EVENT                            = 0X20
HCI_LE_TRANSMIT_POWER_REPORTING_EVENT                       = 0X21
HCI_LE_BIGINFO_ADVERTISING_REPORT_EVENT                     = 0X22
HCI_LE_SUBRATE_CHANGE_EVENT                                 = 0X23
HCI_LE_PERIODIC_ADVERTISING_SYNC_ESTABLISHED_V2_EVENT       = 0X24
HCI_LE_PERIODIC_ADVERTISING_REPORT_V2_EVENT                 = 0X25
HCI_LE_PERIODIC_ADVERTISING_SYNC_TRANSFER_RECEIVED_V2_EVENT = 0X26
HCI_LE_PERIODIC_ADVERTISING_SUBEVENT_DATA_REQUEST_EVENT     = 0X27
HCI_LE_PERIODIC_ADVERTISING_RESPONSE_REPORT_EVENT           = 0X28
HCI_LE_ENHANCED_CONNECTION_COMPLETE_V2_EVENT                = 0X29
HCI_LE_READ_ALL_REMOTE_FEATURES_COMPLETE_EVENT              = 0x2A
HCI_LE_CIS_ESTABLISHED_V2_EVENT                             = 0x2B
HCI_LE_CS_READ_REMOTE_SUPPORTED_CAPABILITIES_COMPLETE_EVENT = 0x2C
HCI_LE_CS_READ_REMOTE_FAE_TABLE_COMPLETE_EVENT              = 0x2D
HCI_LE_CS_SECURITY_ENABLE_COMPLETE_EVENT                    = 0x2E
HCI_LE_CS_CONFIG_COMPLETE_EVENT                             = 0x2F
HCI_LE_CS_PROCEDURE_ENABLE_COMPLETE_EVENT                   = 0x30
HCI_LE_CS_SUBEVENT_RESULT_EVENT                             = 0x31
HCI_LE_CS_SUBEVENT_RESULT_CONTINUE_EVENT                    = 0x32
HCI_LE_CS_TEST_END_COMPLETE_EVENT                           = 0x33
HCI_LE_MONITORED_ADVERTISERS_REPORT_EVENT                   = 0x34
HCI_LE_FRAME_SPACE_UPDATE_EVENT                             = 0x35



# HCI Command
HCI_INQUIRY_COMMAND                                                      = hci_command_op_code(0x01, 0x0001)
HCI_INQUIRY_CANCEL_COMMAND                                               = hci_command_op_code(0x01, 0x0002)
HCI_PERIODIC_INQUIRY_MODE_COMMAND                                        = hci_command_op_code(0x01, 0x0003)
HCI_EXIT_PERIODIC_INQUIRY_MODE_COMMAND                                   = hci_command_op_code(0x01, 0x0004)
HCI_CREATE_CONNECTION_COMMAND                                            = hci_command_op_code(0x01, 0x0005)
HCI_DISCONNECT_COMMAND                                                   = hci_command_op_code(0x01, 0x0006)
HCI_CREATE_CONNECTION_CANCEL_COMMAND                                     = hci_command_op_code(0x01, 0x0008)
HCI_ACCEPT_CONNECTION_REQUEST_COMMAND                                    = hci_command_op_code(0x01, 0x0009)
HCI_REJECT_CONNECTION_REQUEST_COMMAND                                    = hci_command_op_code(0x01, 0x000A)
HCI_LINK_KEY_REQUEST_REPLY_COMMAND                                       = hci_command_op_code(0x01, 0x000B)
HCI_LINK_KEY_REQUEST_NEGATIVE_REPLY_COMMAND                              = hci_command_op_code(0x01, 0x000C)
HCI_PIN_CODE_REQUEST_REPLY_COMMAND                                       = hci_command_op_code(0x01, 0x000D)
HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND                              = hci_command_op_code(0x01, 0x000E)
HCI_CHANGE_CONNECTION_PACKET_TYPE_COMMAND                                = hci_command_op_code(0x01, 0x000F)
HCI_AUTHENTICATION_REQUESTED_COMMAND                                     = hci_command_op_code(0x01, 0x0011)
HCI_SET_CONNECTION_ENCRYPTION_COMMAND                                    = hci_command_op_code(0x01, 0x0013)
HCI_CHANGE_CONNECTION_LINK_KEY_COMMAND                                   = hci_command_op_code(0x01, 0x0015)
HCI_LINK_KEY_SELECTION_COMMAND                                           = hci_command_op_code(0x01, 0x0017)
HCI_REMOTE_NAME_REQUEST_COMMAND                                          = hci_command_op_code(0x01, 0x0019)
HCI_REMOTE_NAME_REQUEST_CANCEL_COMMAND                                   = hci_command_op_code(0x01, 0x001A)
HCI_READ_REMOTE_SUPPORTED_FEATURES_COMMAND                               = hci_command_op_code(0x01, 0x001B)
HCI_READ_REMOTE_EXTENDED_FEATURES_COMMAND                                = hci_command_op_code(0x01, 0x001C)
HCI_READ_REMOTE_VERSION_INFORMATION_COMMAND                              = hci_command_op_code(0x01, 0x001D)
HCI_READ_CLOCK_OFFSET_COMMAND                                            = hci_command_op_code(0x01, 0x001F)
HCI_READ_LMP_HANDLE_COMMAND                                              = hci_command_op_code(0x01, 0x0020)
HCI_SETUP_SYNCHRONOUS_CONNECTION_COMMAND                                 = hci_command_op_code(0x01, 0x0028)
HCI_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND                        = hci_command_op_code(0x01, 0x0029)
HCI_REJECT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND                        = hci_command_op_code(0x01, 0x002A)
HCI_IO_CAPABILITY_REQUEST_REPLY_COMMAND                                  = hci_command_op_code(0x01, 0x002B)
HCI_USER_CONFIRMATION_REQUEST_REPLY_COMMAND                              = hci_command_op_code(0x01, 0x002C)
HCI_USER_CONFIRMATION_REQUEST_NEGATIVE_REPLY_COMMAND                     = hci_command_op_code(0x01, 0x002D)
HCI_USER_PASSKEY_REQUEST_REPLY_COMMAND                                   = hci_command_op_code(0x01, 0x002E)
HCI_USER_PASSKEY_REQUEST_NEGATIVE_REPLY_COMMAND                          = hci_command_op_code(0x01, 0x002F)
HCI_REMOTE_OOB_DATA_REQUEST_REPLY_COMMAND                                = hci_command_op_code(0x01, 0x0030)
HCI_REMOTE_OOB_DATA_REQUEST_NEGATIVE_REPLY_COMMAND                       = hci_command_op_code(0x01, 0x0033)
HCI_IO_CAPABILITY_REQUEST_NEGATIVE_REPLY_COMMAND                         = hci_command_op_code(0x01, 0x0034)
HCI_ENHANCED_SETUP_SYNCHRONOUS_CONNECTION_COMMAND                        = hci_command_op_code(0x01, 0x003D)
HCI_ENHANCED_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND               = hci_command_op_code(0x01, 0x003E)
HCI_TRUNCATED_PAGE_COMMAND                                               = hci_command_op_code(0x01, 0x003F)
HCI_TRUNCATED_PAGE_CANCEL_COMMAND                                        = hci_command_op_code(0x01, 0x0040)
HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_COMMAND                      = hci_command_op_code(0x01, 0x0041)
HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVE_COMMAND              = hci_command_op_code(0x01, 0x0042)
HCI_START_SYNCHRONIZATION_TRAIN_COMMAND                                  = hci_command_op_code(0x01, 0x0043)
HCI_RECEIVE_SYNCHRONIZATION_TRAIN_COMMAND                                = hci_command_op_code(0x01, 0x0044)
HCI_REMOTE_OOB_EXTENDED_DATA_REQUEST_REPLY_COMMAND                       = hci_command_op_code(0x01, 0x0045)
HCI_HOLD_MODE_COMMAND                                                    = hci_command_op_code(0x02, 0x0001)
HCI_SNIFF_MODE_COMMAND                                                   = hci_command_op_code(0x02, 0x0003)
HCI_EXIT_SNIFF_MODE_COMMAND                                              = hci_command_op_code(0x02, 0x0004)
HCI_QOS_SETUP_COMMAND                                                    = hci_command_op_code(0x02, 0x0007)
HCI_ROLE_DISCOVERY_COMMAND                                               = hci_command_op_code(0x02, 0x0009)
HCI_SWITCH_ROLE_COMMAND                                                  = hci_command_op_code(0x02, 0x000B)
HCI_READ_LINK_POLICY_SETTINGS_COMMAND                                    = hci_command_op_code(0x02, 0x000C)
HCI_WRITE_LINK_POLICY_SETTINGS_COMMAND                                   = hci_command_op_code(0x02, 0x000D)
HCI_READ_DEFAULT_LINK_POLICY_SETTINGS_COMMAND                            = hci_command_op_code(0x02, 0x000E)
HCI_WRITE_DEFAULT_LINK_POLICY_SETTINGS_COMMAND                           = hci_command_op_code(0x02, 0x000F)
HCI_FLOW_SPECIFICATION_COMMAND                                           = hci_command_op_code(0x02, 0x0010)
HCI_SNIFF_SUBRATING_COMMAND                                              = hci_command_op_code(0x02, 0x0011)
HCI_SET_EVENT_MASK_COMMAND                                               = hci_command_op_code(0x03, 0x0001)
HCI_RESET_COMMAND                                                        = hci_command_op_code(0x03, 0x0003)
HCI_SET_EVENT_FILTER_COMMAND                                             = hci_command_op_code(0x03, 0x0005)
HCI_FLUSH_COMMAND                                                        = hci_command_op_code(0x03, 0x0008)
HCI_READ_PIN_TYPE_COMMAND                                                = hci_command_op_code(0x03, 0x0009)
HCI_WRITE_PIN_TYPE_COMMAND                                               = hci_command_op_code(0x03, 0x000A)
HCI_READ_STORED_LINK_KEY_COMMAND                                         = hci_command_op_code(0x03, 0x000D)
HCI_WRITE_STORED_LINK_KEY_COMMAND                                        = hci_command_op_code(0x03, 0x0011)
HCI_DELETE_STORED_LINK_KEY_COMMAND                                       = hci_command_op_code(0x03, 0x0012)
HCI_WRITE_LOCAL_NAME_COMMAND                                             = hci_command_op_code(0x03, 0x0013)
HCI_READ_LOCAL_NAME_COMMAND                                              = hci_command_op_code(0x03, 0x0014)
HCI_READ_CONNECTION_ACCEPT_TIMEOUT_COMMAND                               = hci_command_op_code(0x03, 0x0015)
HCI_WRITE_CONNECTION_ACCEPT_TIMEOUT_COMMAND                              = hci_command_op_code(0x03, 0x0016)
HCI_READ_PAGE_TIMEOUT_COMMAND                                            = hci_command_op_code(0x03, 0x0017)
HCI_WRITE_PAGE_TIMEOUT_COMMAND                                           = hci_command_op_code(0x03, 0x0018)
HCI_READ_SCAN_ENABLE_COMMAND                                             = hci_command_op_code(0x03, 0x0019)
HCI_WRITE_SCAN_ENABLE_COMMAND                                            = hci_command_op_code(0x03, 0x001A)
HCI_READ_PAGE_SCAN_ACTIVITY_COMMAND                                      = hci_command_op_code(0x03, 0x001B)
HCI_WRITE_PAGE_SCAN_ACTIVITY_COMMAND                                     = hci_command_op_code(0x03, 0x001C)
HCI_READ_INQUIRY_SCAN_ACTIVITY_COMMAND                                   = hci_command_op_code(0x03, 0x001D)
HCI_WRITE_INQUIRY_SCAN_ACTIVITY_COMMAND                                  = hci_command_op_code(0x03, 0x001E)
HCI_READ_AUTHENTICATION_ENABLE_COMMAND                                   = hci_command_op_code(0x03, 0x001F)
HCI_WRITE_AUTHENTICATION_ENABLE_COMMAND                                  = hci_command_op_code(0x03, 0x0020)
HCI_READ_CLASS_OF_DEVICE_COMMAND                                         = hci_command_op_code(0x03, 0x0023)
HCI_WRITE_CLASS_OF_DEVICE_COMMAND                                        = hci_command_op_code(0x03, 0x0024)
HCI_READ_VOICE_SETTING_COMMAND                                           = hci_command_op_code(0x03, 0x0025)
HCI_WRITE_VOICE_SETTING_COMMAND                                          = hci_command_op_code(0x03, 0x0026)
HCI_READ_AUTOMATIC_FLUSH_TIMEOUT_COMMAND                                 = hci_command_op_code(0x03, 0x0027)
HCI_WRITE_AUTOMATIC_FLUSH_TIMEOUT_COMMAND                                = hci_command_op_code(0x03, 0x0028)
HCI_READ_NUM_BROADCAST_RETRANSMISSIONS_COMMAND                           = hci_command_op_code(0x03, 0x0029)
HCI_WRITE_NUM_BROADCAST_RETRANSMISSIONS_COMMAND                          = hci_command_op_code(0x03, 0x002A)
HCI_READ_HOLD_MODE_ACTIVITY_COMMAND                                      = hci_command_op_code(0x03, 0x002B)
HCI_WRITE_HOLD_MODE_ACTIVITY_COMMAND                                     = hci_command_op_code(0x03, 0x002C)
HCI_READ_TRANSMIT_POWER_LEVEL_COMMAND                                    = hci_command_op_code(0x03, 0x002D)
HCI_READ_SYNCHRONOUS_FLOW_CONTROL_ENABLE_COMMAND                         = hci_command_op_code(0x03, 0x002E)
HCI_WRITE_SYNCHRONOUS_FLOW_CONTROL_ENABLE_COMMAND                        = hci_command_op_code(0x03, 0x002F)
HCI_SET_CONTROLLER_TO_HOST_FLOW_CONTROL_COMMAND                          = hci_command_op_code(0x03, 0x0031)
HCI_HOST_BUFFER_SIZE_COMMAND                                             = hci_command_op_code(0x03, 0x0033)
HCI_HOST_NUMBER_OF_COMPLETED_PACKETS_COMMAND                             = hci_command_op_code(0x03, 0x0035)
HCI_READ_LINK_SUPERVISION_TIMEOUT_COMMAND                                = hci_command_op_code(0x03, 0x0036)
HCI_WRITE_LINK_SUPERVISION_TIMEOUT_COMMAND                               = hci_command_op_code(0x03, 0x0037)
HCI_READ_NUMBER_OF_SUPPORTED_IAC_COMMAND                                 = hci_command_op_code(0x03, 0x0038)
HCI_READ_CURRENT_IAC_LAP_COMMAND                                         = hci_command_op_code(0x03, 0x0039)
HCI_WRITE_CURRENT_IAC_LAP_COMMAND                                        = hci_command_op_code(0x03, 0x003A)
HCI_SET_AFH_HOST_CHANNEL_CLASSIFICATION_COMMAND                          = hci_command_op_code(0x03, 0x003F)
HCI_READ_INQUIRY_SCAN_TYPE_COMMAND                                       = hci_command_op_code(0x03, 0x0042)
HCI_WRITE_INQUIRY_SCAN_TYPE_COMMAND                                      = hci_command_op_code(0x03, 0x0043)
HCI_READ_INQUIRY_MODE_COMMAND                                            = hci_command_op_code(0x03, 0x0044)
HCI_WRITE_INQUIRY_MODE_COMMAND                                           = hci_command_op_code(0x03, 0x0045)
HCI_READ_PAGE_SCAN_TYPE_COMMAND                                          = hci_command_op_code(0x03, 0x0046)
HCI_WRITE_PAGE_SCAN_TYPE_COMMAND                                         = hci_command_op_code(0x03, 0x0047)
HCI_READ_AFH_CHANNEL_ASSESSMENT_MODE_COMMAND                             = hci_command_op_code(0x03, 0x0048)
HCI_WRITE_AFH_CHANNEL_ASSESSMENT_MODE_COMMAND                            = hci_command_op_code(0x03, 0x0049)
HCI_READ_EXTENDED_INQUIRY_RESPONSE_COMMAND                               = hci_command_op_code(0x03, 0x0051)
HCI_WRITE_EXTENDED_INQUIRY_RESPONSE_COMMAND                              = hci_command_op_code(0x03, 0x0052)
HCI_REFRESH_ENCRYPTION_KEY_COMMAND                                       = hci_command_op_code(0x03, 0x0053)
HCI_READ_SIMPLE_PAIRING_MODE_COMMAND                                     = hci_command_op_code(0x03, 0x0055)
HCI_WRITE_SIMPLE_PAIRING_MODE_COMMAND                                    = hci_command_op_code(0x03, 0x0056)
HCI_READ_LOCAL_OOB_DATA_COMMAND                                          = hci_command_op_code(0x03, 0x0057)
HCI_READ_INQUIRY_RESPONSE_TRANSMIT_POWER_LEVEL_COMMAND                   = hci_command_op_code(0x03, 0x0058)
HCI_WRITE_INQUIRY_TRANSMIT_POWER_LEVEL_COMMAND                           = hci_command_op_code(0x03, 0x0059)
HCI_READ_DEFAULT_ERRONEOUS_DATA_REPORTING_COMMAND                        = hci_command_op_code(0x03, 0x005A)
HCI_WRITE_DEFAULT_ERRONEOUS_DATA_REPORTING_COMMAND                       = hci_command_op_code(0x03, 0x005B)
HCI_ENHANCED_FLUSH_COMMAND                                               = hci_command_op_code(0x03, 0x005F)
HCI_SEND_KEYPRESS_NOTIFICATION_COMMAND                                   = hci_command_op_code(0x03, 0x0060)
HCI_SET_EVENT_MASK_PAGE_2_COMMAND                                        = hci_command_op_code(0x03, 0x0063)
HCI_READ_FLOW_CONTROL_MODE_COMMAND                                       = hci_command_op_code(0x03, 0x0066)
HCI_WRITE_FLOW_CONTROL_MODE_COMMAND                                      = hci_command_op_code(0x03, 0x0067)
HCI_READ_ENHANCED_TRANSMIT_POWER_LEVEL_COMMAND                           = hci_command_op_code(0x03, 0x0068)
HCI_READ_LE_HOST_SUPPORT_COMMAND                                         = hci_command_op_code(0x03, 0x006C)
HCI_WRITE_LE_HOST_SUPPORT_COMMAND                                        = hci_command_op_code(0x03, 0x006D)
HCI_SET_MWS_CHANNEL_PARAMETERS_COMMAND                                   = hci_command_op_code(0x03, 0x006E)
HCI_SET_EXTERNAL_FRAME_CONFIGURATION_COMMAND                             = hci_command_op_code(0x03, 0x006F)
HCI_SET_MWS_SIGNALING_COMMAND                                            = hci_command_op_code(0x03, 0x0070)
HCI_SET_MWS_TRANSPORT_LAYER_COMMAND                                      = hci_command_op_code(0x03, 0x0071)
HCI_SET_MWS_SCAN_FREQUENCY_TABLE_COMMAND                                 = hci_command_op_code(0x03, 0x0072)
HCI_SET_MWS_PATTERN_CONFIGURATION_COMMAND                                = hci_command_op_code(0x03, 0x0073)
HCI_SET_RESERVED_LT_ADDR_COMMAND                                         = hci_command_op_code(0x03, 0x0074)
HCI_DELETE_RESERVED_LT_ADDR_COMMAND                                      = hci_command_op_code(0x03, 0x0075)
HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_DATA_COMMAND                 = hci_command_op_code(0x03, 0x0076)
HCI_READ_SYNCHRONIZATION_TRAIN_PARAMETERS_COMMAND                        = hci_command_op_code(0x03, 0x0077)
HCI_WRITE_SYNCHRONIZATION_TRAIN_PARAMETERS_COMMAND                       = hci_command_op_code(0x03, 0x0078)
HCI_READ_SECURE_CONNECTIONS_HOST_SUPPORT_COMMAND                         = hci_command_op_code(0x03, 0x0079)
HCI_WRITE_SECURE_CONNECTIONS_HOST_SUPPORT_COMMAND                        = hci_command_op_code(0x03, 0x007A)
HCI_READ_AUTHENTICATED_PAYLOAD_TIMEOUT_COMMAND                           = hci_command_op_code(0x03, 0x007B)
HCI_WRITE_AUTHENTICATED_PAYLOAD_TIMEOUT_COMMAND                          = hci_command_op_code(0x03, 0x007C)
HCI_READ_LOCAL_OOB_EXTENDED_DATA_COMMAND                                 = hci_command_op_code(0x03, 0x007D)
HCI_READ_EXTENDED_PAGE_TIMEOUT_COMMAND                                   = hci_command_op_code(0x03, 0x007E)
HCI_WRITE_EXTENDED_PAGE_TIMEOUT_COMMAND                                  = hci_command_op_code(0x03, 0x007F)
HCI_READ_EXTENDED_INQUIRY_LENGTH_COMMAND                                 = hci_command_op_code(0x03, 0x0080)
HCI_WRITE_EXTENDED_INQUIRY_LENGTH_COMMAND                                = hci_command_op_code(0x03, 0x0081)
HCI_SET_ECOSYSTEM_BASE_INTERVAL_COMMAND                                  = hci_command_op_code(0x03, 0x0082)
HCI_CONFIGURE_DATA_PATH_COMMAND                                          = hci_command_op_code(0x03, 0x0083)
HCI_SET_MIN_ENCRYPTION_KEY_SIZE_COMMAND                                  = hci_command_op_code(0x03, 0x0084)
HCI_READ_LOCAL_VERSION_INFORMATION_COMMAND                               = hci_command_op_code(0x04, 0x0001)
HCI_READ_LOCAL_SUPPORTED_COMMANDS_COMMAND                                = hci_command_op_code(0x04, 0x0002)
HCI_READ_LOCAL_SUPPORTED_FEATURES_COMMAND                                = hci_command_op_code(0x04, 0x0003)
HCI_READ_LOCAL_EXTENDED_FEATURES_COMMAND                                 = hci_command_op_code(0x04, 0x0004)
HCI_READ_BUFFER_SIZE_COMMAND                                             = hci_command_op_code(0x04, 0x0005)
HCI_READ_BD_ADDR_COMMAND                                                 = hci_command_op_code(0x04, 0x0009)
HCI_READ_DATA_BLOCK_SIZE_COMMAND                                         = hci_command_op_code(0x04, 0x000A)
HCI_READ_LOCAL_SUPPORTED_CODECS_COMMAND                                  = hci_command_op_code(0x04, 0x000B)
HCI_READ_LOCAL_SIMPLE_PAIRING_OPTIONS_COMMAND                            = hci_command_op_code(0x04, 0x000C)
HCI_READ_LOCAL_SUPPORTED_CODECS_V2_COMMAND                               = hci_command_op_code(0x04, 0x000D)
HCI_READ_LOCAL_SUPPORTED_CODEC_CAPABILITIES_COMMAND                      = hci_command_op_code(0x04, 0x000E)
HCI_READ_LOCAL_SUPPORTED_CONTROLLER_DELAY_COMMAND                        = hci_command_op_code(0x04, 0x000F)
HCI_READ_FAILED_CONTACT_COUNTER_COMMAND                                  = hci_command_op_code(0x05, 0x0001)
HCI_RESET_FAILED_CONTACT_COUNTER_COMMAND                                 = hci_command_op_code(0x05, 0x0002)
HCI_READ_LINK_QUALITY_COMMAND                                            = hci_command_op_code(0x05, 0x0003)
HCI_READ_RSSI_COMMAND                                                    = hci_command_op_code(0x05, 0x0005)
HCI_READ_AFH_CHANNEL_MAP_COMMAND                                         = hci_command_op_code(0x05, 0x0006)
HCI_READ_CLOCK_COMMAND                                                   = hci_command_op_code(0x05, 0x0007)
HCI_READ_ENCRYPTION_KEY_SIZE_COMMAND                                     = hci_command_op_code(0x05, 0x0008)
HCI_GET_MWS_TRANSPORT_LAYER_CONFIGURATION_COMMAND                        = hci_command_op_code(0x05, 0x000C)
HCI_SET_TRIGGERED_CLOCK_CAPTURE_COMMAND                                  = hci_command_op_code(0x05, 0x000D)
HCI_READ_LOOPBACK_MODE_COMMAND                                           = hci_command_op_code(0x06, 0x0001)
HCI_WRITE_LOOPBACK_MODE_COMMAND                                          = hci_command_op_code(0x06, 0x0002)
HCI_ENABLE_DEVICE_UNDER_TEST_MODE_COMMAND                                = hci_command_op_code(0x06, 0x0003)
HCI_WRITE_SIMPLE_PAIRING_DEBUG_MODE_COMMAND                              = hci_command_op_code(0x06, 0x0004)
HCI_WRITE_SECURE_CONNECTIONS_TEST_MODE_COMMAND                           = hci_command_op_code(0x06, 0x000A)
HCI_LE_SET_EVENT_MASK_COMMAND                                            = hci_command_op_code(0x08, 0x0001)
HCI_LE_READ_BUFFER_SIZE_COMMAND                                          = hci_command_op_code(0x08, 0x0002)
HCI_LE_READ_LOCAL_SUPPORTED_FEATURES_COMMAND                             = hci_command_op_code(0x08, 0x0003)
HCI_LE_SET_RANDOM_ADDRESS_COMMAND                                        = hci_command_op_code(0x08, 0x0005)
HCI_LE_SET_ADVERTISING_PARAMETERS_COMMAND                                = hci_command_op_code(0x08, 0x0006)
HCI_LE_READ_ADVERTISING_PHYSICAL_CHANNEL_TX_POWER_COMMAND                = hci_command_op_code(0x08, 0x0007)
HCI_LE_SET_ADVERTISING_DATA_COMMAND                                      = hci_command_op_code(0x08, 0x0008)
HCI_LE_SET_SCAN_RESPONSE_DATA_COMMAND                                    = hci_command_op_code(0x08, 0x0009)
HCI_LE_SET_ADVERTISING_ENABLE_COMMAND                                    = hci_command_op_code(0x08, 0x000A)
HCI_LE_SET_SCAN_PARAMETERS_COMMAND                                       = hci_command_op_code(0x08, 0x000B)
HCI_LE_SET_SCAN_ENABLE_COMMAND                                           = hci_command_op_code(0x08, 0x000C)
HCI_LE_CREATE_CONNECTION_COMMAND                                         = hci_command_op_code(0x08, 0x000D)
HCI_LE_CREATE_CONNECTION_CANCEL_COMMAND                                  = hci_command_op_code(0x08, 0x000E)
HCI_LE_READ_FILTER_ACCEPT_LIST_SIZE_COMMAND                              = hci_command_op_code(0x08, 0x000F)
HCI_LE_CLEAR_FILTER_ACCEPT_LIST_COMMAND                                  = hci_command_op_code(0x08, 0x0010)
HCI_LE_ADD_DEVICE_TO_FILTER_ACCEPT_LIST_COMMAND                          = hci_command_op_code(0x08, 0x0011)
HCI_LE_REMOVE_DEVICE_FROM_FILTER_ACCEPT_LIST_COMMAND                     = hci_command_op_code(0x08, 0x0012)
HCI_LE_CONNECTION_UPDATE_COMMAND                                         = hci_command_op_code(0x08, 0x0013)
HCI_LE_SET_HOST_CHANNEL_CLASSIFICATION_COMMAND                           = hci_command_op_code(0x08, 0x0014)
HCI_LE_READ_CHANNEL_MAP_COMMAND                                          = hci_command_op_code(0x08, 0x0015)
HCI_LE_READ_REMOTE_FEATURES_COMMAND                                      = hci_command_op_code(0x08, 0x0016)
HCI_LE_ENCRYPT_COMMAND                                                   = hci_command_op_code(0x08, 0x0017)
HCI_LE_RAND_COMMAND                                                      = hci_command_op_code(0x08, 0x0018)
HCI_LE_ENABLE_ENCRYPTION_COMMAND                                         = hci_command_op_code(0x08, 0x0019)
HCI_LE_LONG_TERM_KEY_REQUEST_REPLY_COMMAND                               = hci_command_op_code(0x08, 0x001A)
HCI_LE_LONG_TERM_KEY_REQUEST_NEGATIVE_REPLY_COMMAND                      = hci_command_op_code(0x08, 0x001B)
HCI_LE_READ_SUPPORTED_STATES_COMMAND                                     = hci_command_op_code(0x08, 0x001C)
HCI_LE_RECEIVER_TEST_COMMAND                                             = hci_command_op_code(0x08, 0x001D)
HCI_LE_TRANSMITTER_TEST_COMMAND                                          = hci_command_op_code(0x08, 0x001E)
HCI_LE_TEST_END_COMMAND                                                  = hci_command_op_code(0x08, 0x001F)
HCI_LE_REMOTE_CONNECTION_PARAMETER_REQUEST_REPLY_COMMAND                 = hci_command_op_code(0x08, 0x0020)
HCI_LE_REMOTE_CONNECTION_PARAMETER_REQUEST_NEGATIVE_REPLY_COMMAND        = hci_command_op_code(0x08, 0x0021)
HCI_LE_SET_DATA_LENGTH_COMMAND                                           = hci_command_op_code(0x08, 0x0022)
HCI_LE_READ_SUGGESTED_DEFAULT_DATA_LENGTH_COMMAND                        = hci_command_op_code(0x08, 0x0023)
HCI_LE_WRITE_SUGGESTED_DEFAULT_DATA_LENGTH_COMMAND                       = hci_command_op_code(0x08, 0x0024)
HCI_LE_READ_LOCAL_P_256_PUBLIC_KEY_COMMAND                               = hci_command_op_code(0x08, 0x0025)
HCI_LE_GENERATE_DHKEY_COMMAND                                            = hci_command_op_code(0x08, 0x0026)
HCI_LE_ADD_DEVICE_TO_RESOLVING_LIST_COMMAND                              = hci_command_op_code(0x08, 0x0027)
HCI_LE_REMOVE_DEVICE_FROM_RESOLVING_LIST_COMMAND                         = hci_command_op_code(0x08, 0x0028)
HCI_LE_CLEAR_RESOLVING_LIST_COMMAND                                      = hci_command_op_code(0x08, 0x0029)
HCI_LE_READ_RESOLVING_LIST_SIZE_COMMAND                                  = hci_command_op_code(0x08, 0x002A)
HCI_LE_READ_PEER_RESOLVABLE_ADDRESS_COMMAND                              = hci_command_op_code(0x08, 0x002B)
HCI_LE_READ_LOCAL_RESOLVABLE_ADDRESS_COMMAND                             = hci_command_op_code(0x08, 0x002C)
HCI_LE_SET_ADDRESS_RESOLUTION_ENABLE_COMMAND                             = hci_command_op_code(0x08, 0x002D)
HCI_LE_SET_RESOLVABLE_PRIVATE_ADDRESS_TIMEOUT_COMMAND                    = hci_command_op_code(0x08, 0x002E)
HCI_LE_READ_MAXIMUM_DATA_LENGTH_COMMAND                                  = hci_command_op_code(0x08, 0x002F)
HCI_LE_READ_PHY_COMMAND                                                  = hci_command_op_code(0x08, 0x0030)
HCI_LE_SET_DEFAULT_PHY_COMMAND                                           = hci_command_op_code(0x08, 0x0031)
HCI_LE_SET_PHY_COMMAND                                                   = hci_command_op_code(0x08, 0x0032)
HCI_LE_RECEIVER_TEST_V2_COMMAND                                          = hci_command_op_code(0x08, 0x0033)
HCI_LE_TRANSMITTER_TEST_V2_COMMAND                                       = hci_command_op_code(0x08, 0x0034)
HCI_LE_SET_ADVERTISING_SET_RANDOM_ADDRESS_COMMAND                        = hci_command_op_code(0x08, 0x0035)
HCI_LE_SET_EXTENDED_ADVERTISING_PARAMETERS_COMMAND                       = hci_command_op_code(0x08, 0x0036)
HCI_LE_SET_EXTENDED_ADVERTISING_DATA_COMMAND                             = hci_command_op_code(0x08, 0x0037)
HCI_LE_SET_EXTENDED_SCAN_RESPONSE_DATA_COMMAND                           = hci_command_op_code(0x08, 0x0038)
HCI_LE_SET_EXTENDED_ADVERTISING_ENABLE_COMMAND                           = hci_command_op_code(0x08, 0x0039)
HCI_LE_READ_MAXIMUM_ADVERTISING_DATA_LENGTH_COMMAND                      = hci_command_op_code(0x08, 0x003A)
HCI_LE_READ_NUMBER_OF_SUPPORTED_ADVERTISING_SETS_COMMAND                 = hci_command_op_code(0x08, 0x003B)
HCI_LE_REMOVE_ADVERTISING_SET_COMMAND                                    = hci_command_op_code(0x08, 0x003C)
HCI_LE_CLEAR_ADVERTISING_SETS_COMMAND                                    = hci_command_op_code(0x08, 0x003D)
HCI_LE_SET_PERIODIC_ADVERTISING_PARAMETERS_COMMAND                       = hci_command_op_code(0x08, 0x003E)
HCI_LE_SET_PERIODIC_ADVERTISING_DATA_COMMAND                             = hci_command_op_code(0x08, 0x003F)
HCI_LE_SET_PERIODIC_ADVERTISING_ENABLE_COMMAND                           = hci_command_op_code(0x08, 0x0040)
HCI_LE_SET_EXTENDED_SCAN_PARAMETERS_COMMAND                              = hci_command_op_code(0x08, 0x0041)
HCI_LE_SET_EXTENDED_SCAN_ENABLE_COMMAND                                  = hci_command_op_code(0x08, 0x0042)
HCI_LE_EXTENDED_CREATE_CONNECTION_COMMAND                                = hci_command_op_code(0x08, 0x0043)
HCI_LE_PERIODIC_ADVERTISING_CREATE_SYNC_COMMAND                          = hci_command_op_code(0x08, 0x0044)
HCI_LE_PERIODIC_ADVERTISING_CREATE_SYNC_CANCEL_COMMAND                   = hci_command_op_code(0x08, 0x0045)
HCI_LE_PERIODIC_ADVERTISING_TERMINATE_SYNC_COMMAND                       = hci_command_op_code(0x08, 0x0046)
HCI_LE_ADD_DEVICE_TO_PERIODIC_ADVERTISER_LIST_COMMAND                    = hci_command_op_code(0x08, 0x0047)
HCI_LE_REMOVE_DEVICE_FROM_PERIODIC_ADVERTISER_LIST_COMMAND               = hci_command_op_code(0x08, 0x0048)
HCI_LE_CLEAR_PERIODIC_ADVERTISER_LIST_COMMAND                            = hci_command_op_code(0x08, 0x0049)
HCI_LE_READ_PERIODIC_ADVERTISER_LIST_SIZE_COMMAND                        = hci_command_op_code(0x08, 0x004A)
HCI_LE_READ_TRANSMIT_POWER_COMMAND                                       = hci_command_op_code(0x08, 0x004B)
HCI_LE_READ_RF_PATH_COMPENSATION_COMMAND                                 = hci_command_op_code(0x08, 0x004C)
HCI_LE_WRITE_RF_PATH_COMPENSATION_COMMAND                                = hci_command_op_code(0x08, 0x004D)
HCI_LE_SET_PRIVACY_MODE_COMMAND                                          = hci_command_op_code(0x08, 0x004E)
HCI_LE_RECEIVER_TEST_V3_COMMAND                                          = hci_command_op_code(0x08, 0x004F)
HCI_LE_TRANSMITTER_TEST_V3_COMMAND                                       = hci_command_op_code(0x08, 0x0050)
HCI_LE_SET_CONNECTIONLESS_CTE_TRANSMIT_PARAMETERS_COMMAND                = hci_command_op_code(0x08, 0x0051)
HCI_LE_SET_CONNECTIONLESS_CTE_TRANSMIT_ENABLE_COMMAND                    = hci_command_op_code(0x08, 0x0052)
HCI_LE_SET_CONNECTIONLESS_IQ_SAMPLING_ENABLE_COMMAND                     = hci_command_op_code(0x08, 0x0053)
HCI_LE_SET_CONNECTION_CTE_RECEIVE_PARAMETERS_COMMAND                     = hci_command_op_code(0x08, 0x0054)
HCI_LE_SET_CONNECTION_CTE_TRANSMIT_PARAMETERS_COMMAND                    = hci_command_op_code(0x08, 0x0055)
HCI_LE_CONNECTION_CTE_REQUEST_ENABLE_COMMAND                             = hci_command_op_code(0x08, 0x0056)
HCI_LE_CONNECTION_CTE_RESPONSE_ENABLE_COMMAND                            = hci_command_op_code(0x08, 0x0057)
HCI_LE_READ_ANTENNA_INFORMATION_COMMAND                                  = hci_command_op_code(0x08, 0x0058)
HCI_LE_SET_PERIODIC_ADVERTISING_RECEIVE_ENABLE_COMMAND                   = hci_command_op_code(0x08, 0x0059)
HCI_LE_PERIODIC_ADVERTISING_SYNC_TRANSFER_COMMAND                        = hci_command_op_code(0x08, 0x005A)
HCI_LE_PERIODIC_ADVERTISING_SET_INFO_TRANSFER_COMMAND                    = hci_command_op_code(0x08, 0x005B)
HCI_LE_SET_PERIODIC_ADVERTISING_SYNC_TRANSFER_PARAMETERS_COMMAND         = hci_command_op_code(0x08, 0x005C)
HCI_LE_SET_DEFAULT_PERIODIC_ADVERTISING_SYNC_TRANSFER_PARAMETERS_COMMAND = hci_command_op_code(0x08, 0x005D)
HCI_LE_GENERATE_DHKEY_V2_COMMAND                                         = hci_command_op_code(0x08, 0x005E)
HCI_LE_MODIFY_SLEEP_CLOCK_ACCURACY_COMMAND                               = hci_command_op_code(0x08, 0x005F)
HCI_LE_READ_BUFFER_SIZE_V2_COMMAND                                       = hci_command_op_code(0x08, 0x0060)
HCI_LE_READ_ISO_TX_SYNC_COMMAND                                          = hci_command_op_code(0x08, 0x0061)
HCI_LE_SET_CIG_PARAMETERS_COMMAND                                        = hci_command_op_code(0x08, 0x0062)
HCI_LE_SET_CIG_PARAMETERS_TEST_COMMAND                                   = hci_command_op_code(0x08, 0x0063)
HCI_LE_CREATE_CIS_COMMAND                                                = hci_command_op_code(0x08, 0x0064)
HCI_LE_REMOVE_CIG_COMMAND                                                = hci_command_op_code(0x08, 0x0065)
HCI_LE_ACCEPT_CIS_REQUEST_COMMAND                                        = hci_command_op_code(0x08, 0x0066)
HCI_LE_REJECT_CIS_REQUEST_COMMAND                                        = hci_command_op_code(0x08, 0x0067)
HCI_LE_CREATE_BIG_COMMAND                                                = hci_command_op_code(0x08, 0x0068)
HCI_LE_CREATE_BIG_TEST_COMMAND                                           = hci_command_op_code(0x08, 0x0069)
HCI_LE_TERMINATE_BIG_COMMAND                                             = hci_command_op_code(0x08, 0x006A)
HCI_LE_BIG_CREATE_SYNC_COMMAND                                           = hci_command_op_code(0x08, 0x006B)
HCI_LE_BIG_TERMINATE_SYNC_COMMAND                                        = hci_command_op_code(0x08, 0x006C)
HCI_LE_REQUEST_PEER_SCA_COMMAND                                          = hci_command_op_code(0x08, 0x006D)
HCI_LE_SETUP_ISO_DATA_PATH_COMMAND                                       = hci_command_op_code(0x08, 0x006E)
HCI_LE_REMOVE_ISO_DATA_PATH_COMMAND                                      = hci_command_op_code(0x08, 0x006F)
HCI_LE_ISO_TRANSMIT_TEST_COMMAND                                         = hci_command_op_code(0x08, 0x0070)
HCI_LE_ISO_RECEIVE_TEST_COMMAND                                          = hci_command_op_code(0x08, 0x0071)
HCI_LE_ISO_READ_TEST_COUNTERS_COMMAND                                    = hci_command_op_code(0x08, 0x0072)
HCI_LE_ISO_TEST_END_COMMAND                                              = hci_command_op_code(0x08, 0x0073)
HCI_LE_SET_HOST_FEATURE_COMMAND                                          = hci_command_op_code(0x08, 0x0074)
HCI_LE_READ_ISO_LINK_QUALITY_COMMAND                                     = hci_command_op_code(0x08, 0x0075)
HCI_LE_ENHANCED_READ_TRANSMIT_POWER_LEVEL_COMMAND                        = hci_command_op_code(0x08, 0x0076)
HCI_LE_READ_REMOTE_TRANSMIT_POWER_LEVEL_COMMAND                          = hci_command_op_code(0x08, 0x0077)
HCI_LE_SET_PATH_LOSS_REPORTING_PARAMETERS_COMMAND                        = hci_command_op_code(0x08, 0x0078)
HCI_LE_SET_PATH_LOSS_REPORTING_ENABLE_COMMAND                            = hci_command_op_code(0x08, 0x0079)
HCI_LE_SET_TRANSMIT_POWER_REPORTING_ENABLE_COMMAND                       = hci_command_op_code(0x08, 0x007A)
HCI_LE_TRANSMITTER_TEST_V4_COMMAND                                       = hci_command_op_code(0x08, 0x007B)
HCI_LE_SET_DATA_RELATED_ADDRESS_CHANGES_COMMAND                          = hci_command_op_code(0x08, 0x007C)
HCI_LE_SET_DEFAULT_SUBRATE_COMMAND                                       = hci_command_op_code(0x08, 0x007D)
HCI_LE_SUBRATE_REQUEST_COMMAND                                           = hci_command_op_code(0x08, 0x007E)
HCI_LE_SET_EXTENDED_ADVERTISING_PARAMETERS_V2_COMMAND                    = hci_command_op_code(0x08, 0x007F)
HCI_LE_SET_DECISION_DATA_COMMAND                                         = hci_command_op_code(0x08, 0x0080)
HCI_LE_SET_DECISION_INSTRUCTIONS_COMMAND                                 = hci_command_op_code(0x08, 0x0081)
HCI_LE_SET_PERIODIC_ADVERTISING_SUBEVENT_DATA_COMMAND                    = hci_command_op_code(0x08, 0x0082)
HCI_LE_SET_PERIODIC_ADVERTISING_RESPONSE_DATA_COMMAND                    = hci_command_op_code(0x08, 0x0083)
HCI_LE_SET_PERIODIC_SYNC_SUBEVENT_COMMAND                                = hci_command_op_code(0x08, 0x0084)
HCI_LE_EXTENDED_CREATE_CONNECTION_V2_COMMAND                             = hci_command_op_code(0x08, 0x0085)
HCI_LE_SET_PERIODIC_ADVERTISING_PARAMETERS_V2_COMMAND                    = hci_command_op_code(0x08, 0x0086)
HCI_LE_READ_ALL_LOCAL_SUPPORTED_FEATURES_COMMAND                         = hci_command_op_code(0x08, 0x0087)
HCI_LE_READ_ALL_REMOTE_FEATURES_COMMAND                                  = hci_command_op_code(0x08, 0x0088)
HCI_LE_CS_READ_LOCAL_SUPPORTED_CAPABILITIES_COMMAND                      = hci_command_op_code(0x08, 0x0089)
HCI_LE_CS_READ_REMOTE_SUPPORTED_CAPABILITIES_COMMAND                     = hci_command_op_code(0x08, 0x008A)
HCI_LE_CS_WRITE_CACHED_REMOTE_SUPPORTED_CAPABILITIES_COMMAND             = hci_command_op_code(0x08, 0x008B)
HCI_LE_CS_SECURITY_ENABLE_COMMAND                                        = hci_command_op_code(0x08, 0x008C)
HCI_LE_CS_SET_DEFAULT_SETTINGS_COMMAND                                   = hci_command_op_code(0x08, 0x008D)
HCI_LE_CS_READ_REMOTE_FAE_TABLE_COMMAND                                  = hci_command_op_code(0x08, 0x008E)
HCI_LE_CS_WRITE_CACHED_REMOTE_FAE_TABLE_COMMAND                          = hci_command_op_code(0x08, 0x008F)
HCI_LE_CS_CREATE_CONFIG_COMMAND                                          = hci_command_op_code(0x08, 0x0090)
HCI_LE_CS_REMOVE_CONFIG_COMMAND                                          = hci_command_op_code(0x08, 0x0091)
HCI_LE_CS_SET_CHANNEL_CLASSIFICATION_COMMAND                             = hci_command_op_code(0x08, 0x0092)
HCI_LE_CS_SET_PROCEDURE_PARAMETERS_COMMAND                               = hci_command_op_code(0x08, 0x0093)
HCI_LE_CS_PROCEDURE_ENABLE_COMMAND                                       = hci_command_op_code(0x08, 0x0094)
HCI_LE_CS_TEST_COMMAND                                                   = hci_command_op_code(0x08, 0x0095)
HCI_LE_CS_TEST_END_COMMAND                                               = hci_command_op_code(0x08, 0x0096)
HCI_LE_SET_HOST_FEATURE_V2_COMMAND                                       = hci_command_op_code(0x08, 0x0097)
HCI_LE_ADD_DEVICE_TO_MONITORED_ADVERTISERS_LIST_COMMAND                  = hci_command_op_code(0x08, 0x0098)
HCI_LE_REMOVE_DEVICE_FROM_MONITORED_ADVERTISERS_LIST_COMMAND             = hci_command_op_code(0x08, 0x0099)
HCI_LE_CLEAR_MONITORED_ADVERTISERS_LIST_COMMAND                          = hci_command_op_code(0x08, 0x009A)
HCI_LE_READ_MONITORED_ADVERTISERS_LIST_SIZE_COMMAND                      = hci_command_op_code(0x08, 0x009B)
HCI_LE_ENABLE_MONITORING_ADVERTISERS_COMMAND                             = hci_command_op_code(0x08, 0x009C)
HCI_LE_FRAME_SPACE_UPDATE_COMMAND                                        = hci_command_op_code(0x08, 0x009D)


# HCI Error Codes
# See Bluetooth spec Vol 2, Part D - 1.3 LIST OF ERROR CODES
HCI_SUCCESS                                                                            = 0x00
HCI_UNKNOWN_HCI_COMMAND_ERROR                                                          = 0x01
HCI_UNKNOWN_CONNECTION_IDENTIFIER_ERROR                                                = 0x02
HCI_HARDWARE_FAILURE_ERROR                                                             = 0x03
HCI_PAGE_TIMEOUT_ERROR                                                                 = 0x04
HCI_AUTHENTICATION_FAILURE_ERROR                                                       = 0x05
HCI_PIN_OR_KEY_MISSING_ERROR                                                           = 0x06
HCI_MEMORY_CAPACITY_EXCEEDED_ERROR                                                     = 0x07
HCI_CONNECTION_TIMEOUT_ERROR                                                           = 0x08
HCI_CONNECTION_LIMIT_EXCEEDED_ERROR                                                    = 0x09
HCI_SYNCHRONOUS_CONNECTION_LIMIT_TO_A_DEVICE_EXCEEDED_ERROR                            = 0x0A
HCI_CONNECTION_ALREADY_EXISTS_ERROR                                                    = 0x0B
HCI_COMMAND_DISALLOWED_ERROR                                                           = 0x0C
HCI_CONNECTION_REJECTED_DUE_TO_LIMITED_RESOURCES_ERROR                                 = 0x0D
HCI_CONNECTION_REJECTED_DUE_TO_SECURITY_REASONS_ERROR                                  = 0x0E
HCI_CONNECTION_REJECTED_DUE_TO_UNACCEPTABLE_BD_ADDR_ERROR                              = 0x0F
HCI_CONNECTION_ACCEPT_TIMEOUT_ERROR                                                    = 0x10
HCI_UNSUPPORTED_FEATURE_OR_PARAMETER_VALUE_ERROR                                       = 0x11
HCI_INVALID_HCI_COMMAND_PARAMETERS_ERROR                                               = 0x12
HCI_REMOTE_USER_TERMINATED_CONNECTION_ERROR                                            = 0x13
HCI_REMOTE_DEVICE_TERMINATED_CONNECTION_DUE_TO_LOW_RESOURCES_ERROR                     = 0x14
HCI_REMOTE_DEVICE_TERMINATED_CONNECTION_DUE_TO_POWER_OFF_ERROR                         = 0x15
HCI_CONNECTION_TERMINATED_BY_LOCAL_HOST_ERROR                                          = 0x16
HCI_REPEATED_ATTEMPTS_ERROR                                                            = 0X17
HCI_PAIRING_NOT_ALLOWED_ERROR                                                          = 0X18
HCI_UNKNOWN_LMP_PDU_ERROR                                                              = 0X19
HCI_UNSUPPORTED_REMOTE_FEATURE_ERROR                                                   = 0X1A
HCI_SCO_OFFSET_REJECTED_ERROR                                                          = 0X1B
HCI_SCO_INTERVAL_REJECTED_ERROR                                                        = 0X1C
HCI_SCO_AIR_MODE_REJECTED_ERROR                                                        = 0X1D
HCI_INVALID_LMP_OR_LL_PARAMETERS_ERROR                                                 = 0X1E
HCI_UNSPECIFIED_ERROR_ERROR                                                            = 0X1F
HCI_UNSUPPORTED_LMP_OR_LL_PARAMETER_VALUE_ERROR                                        = 0X20
HCI_ROLE_CHANGE_NOT_ALLOWED_ERROR                                                      = 0X21
HCI_LMP_OR_LL_RESPONSE_TIMEOUT_ERROR                                                   = 0X22
HCI_LMP_ERROR_TRANSACTION_COLLISION_OR_LL_PROCEDURE_COLLISION_ERROR                    = 0X23
HCI_LMP_PDU_NOT_ALLOWED_ERROR                                                          = 0X24
HCI_ENCRYPTION_MODE_NOT_ACCEPTABLE_ERROR                                               = 0X25
HCI_LINK_KEY_CANNOT_BE_CHANGED_ERROR                                                   = 0X26
HCI_REQUESTED_QOS_NOT_SUPPORTED_ERROR                                                  = 0X27
HCI_INSTANT_PASSED_ERROR                                                               = 0X28
HCI_PAIRING_WITH_UNIT_KEY_NOT_SUPPORTED_ERROR                                          = 0X29
HCI_DIFFERENT_TRANSACTION_COLLISION_ERROR                                              = 0X2A
HCI_RESERVED_FOR_FUTURE_USE                                                            = 0X2B
HCI_QOS_UNACCEPTABLE_PARAMETER_ERROR                                                   = 0X2C
HCI_QOS_REJECTED_ERROR                                                                 = 0X2D
HCI_CHANNEL_CLASSIFICATION_NOT_SUPPORTED_ERROR                                         = 0X2E
HCI_INSUFFICIENT_SECURITY_ERROR                                                        = 0X2F
HCI_PARAMETER_OUT_OF_MANDATORY_RANGE_ERROR                                             = 0X30
HCI_ROLE_SWITCH_PENDING_ERROR                                                          = 0X32
HCI_RESERVED_SLOT_VIOLATION_ERROR                                                      = 0X34
HCI_ROLE_SWITCH_FAILED_ERROR                                                           = 0X35
HCI_EXTENDED_INQUIRY_RESPONSE_TOO_LARGE_ERROR                                          = 0X36
HCI_SECURE_SIMPLE_PAIRING_NOT_SUPPORTED_BY_HOST_ERROR                                  = 0X37
HCI_HOST_BUSY_PAIRING_ERROR                                                            = 0X38
HCI_CONNECTION_REJECTED_DUE_TO_NO_SUITABLE_CHANNEL_FOUND_ERROR                         = 0X39
HCI_CONTROLLER_BUSY_ERROR                                                              = 0X3A
HCI_UNACCEPTABLE_CONNECTION_PARAMETERS_ERROR                                           = 0X3B
HCI_ADVERTISING_TIMEOUT_ERROR                                                          = 0X3C
HCI_CONNECTION_TERMINATED_DUE_TO_MIC_FAILURE_ERROR                                     = 0X3D
HCI_CONNECTION_FAILED_TO_BE_ESTABLISHED_ERROR                                          = 0X3E
HCI_COARSE_CLOCK_ADJUSTMENT_REJECTED_BUT_WILL_TRY_TO_ADJUST_USING_CLOCK_DRAGGING_ERROR = 0X40
HCI_TYPE0_SUBMAP_NOT_DEFINED_ERROR                                                     = 0X41
HCI_UNKNOWN_ADVERTISING_IDENTIFIER_ERROR                                               = 0X42
HCI_LIMIT_REACHED_ERROR                                                                = 0X43
HCI_OPERATION_CANCELLED_BY_HOST_ERROR                                                  = 0X44
HCI_PACKET_TOO_LONG_ERROR                                                              = 0X45

HCI_ERROR_NAMES = {
    error_code: error_name for (error_name, error_code) in globals().items()
    if error_name.startswith('HCI_') and error_name.endswith('_ERROR')
}
HCI_ERROR_NAMES[HCI_SUCCESS] = 'HCI_SUCCESS'

# Command Status codes
HCI_COMMAND_STATUS_PENDING = 0


class Phy(SpecableEnum):
    LE_1M    = 1
    LE_2M    = 2
    LE_CODED = 3


# ACL
HCI_ACL_PB_FIRST_NON_FLUSHABLE = 0
HCI_ACL_PB_CONTINUATION        = 1
HCI_ACL_PB_FIRST_FLUSHABLE     = 2
HCI_ACK_PB_COMPLETE_L2CAP      = 3

HCI_LE_PHY_NAMES: dict[int,str] = {
    Phy.LE_1M:    'LE 1M',
    Phy.LE_2M:    'LE 2M',
    Phy.LE_CODED: 'LE Coded'
}

HCI_LE_1M_PHY_BIT    = 0
HCI_LE_2M_PHY_BIT    = 1
HCI_LE_CODED_PHY_BIT = 2

HCI_LE_PHY_BIT_NAMES = ['LE_1M_PHY', 'LE_2M_PHY', 'LE_CODED_PHY']

HCI_LE_PHY_TYPE_TO_BIT: dict[Phy, int] = {
    Phy.LE_1M:    HCI_LE_1M_PHY_BIT,
    Phy.LE_2M:    HCI_LE_2M_PHY_BIT,
    Phy.LE_CODED: HCI_LE_CODED_PHY_BIT,
}


class PhyBit(SpecableFlag):
    LE_1M    = 1 << HCI_LE_1M_PHY_BIT
    LE_2M    = 1 << HCI_LE_2M_PHY_BIT
    LE_CODED = 1 << HCI_LE_CODED_PHY_BIT


class CsRole(SpecableEnum):
    INITIATOR = 0x00
    REFLECTOR = 0x01


class CsRoleMask(enum.IntFlag):
    INITIATOR = 0x01
    REFLECTOR = 0x02


class CsSyncPhy(SpecableEnum):
    LE_1M     = 1
    LE_2M     = 2
    LE_2M_2BT = 3


class CsSyncPhySupported(enum.IntFlag):
    LE_2M     = 0x01
    LE_2M_2BT = 0x02


class RttType(SpecableEnum):
    AA_ONLY = 0x00
    SOUNDING_SEQUENCE_32_BIT = 0x01
    SOUNDING_SEQUENCE_96_BIT = 0x02
    RANDOM_SEQUENCE_32_BIT = 0x03
    RANDOM_SEQUENCE_64_BIT = 0x04
    RANDOM_SEQUENCE_96_BIT = 0x05
    RANDOM_SEQUENCE_128_BIT = 0x06


class CsSnr(SpecableEnum):
    SNR_18_DB = 0x00
    SNR_21_DB = 0x01
    SNR_24_DB = 0x02
    SNR_27_DB = 0x03
    SNR_30_DB = 0x04
    NOT_APPLIED = 0xFF


class CsDoneStatus(SpecableEnum):
    ALL_RESULTS_COMPLETED = 0x00
    PARTIAL = 0x01
    ABORTED = 0x0F


class CsProcedureAbortReason(SpecableEnum):
    NO_ABORT = 0x00
    LOCAL_HOST_OR_REMOTE_REQUEST = 0x01
    CHANNEL_MAP_UPDATE_INSTANT_PASSED = 0x02
    UNSPECIFIED = 0x0F


class CsSubeventAbortReason(SpecableEnum):
    NO_ABORT = 0x00
    LOCAL_HOST_OR_REMOTE_REQUEST = 0x01
    NO_CS_SYNC_RECEIVED = 0x02
    SCHEDULING_CONFLICT_OR_LIMITED_RESOURCES = 0x03
    UNSPECIFIED = 0x0F

class Role(SpecableEnum):
    CENTRAL    = 0
    PERIPHERAL = 1

# For Backward Compatibility.
HCI_CENTRAL_ROLE    = Role.CENTRAL
HCI_PERIPHERAL_ROLE = Role.PERIPHERAL


HCI_LE_1M_PHY    = Phy.LE_1M
HCI_LE_2M_PHY    = Phy.LE_2M
HCI_LE_CODED_PHY = Phy.LE_CODED


# Connection Parameters
HCI_CONNECTION_INTERVAL_MS_PER_UNIT = 1.25
HCI_CONNECTION_LATENCY_MS_PER_UNIT  = 1.25
HCI_SUPERVISION_TIMEOUT_MS_PER_UNIT = 10

# Inquiry LAP
HCI_LIMITED_DEDICATED_INQUIRY_LAP = 0x9E8B00
HCI_GENERAL_INQUIRY_LAP           = 0x9E8B33
HCI_INQUIRY_LAP_NAMES = {
    HCI_LIMITED_DEDICATED_INQUIRY_LAP: 'Limited Dedicated Inquiry',
    HCI_GENERAL_INQUIRY_LAP:           'General Inquiry'
}

# Inquiry Mode
HCI_STANDARD_INQUIRY_MODE  = 0x00
HCI_INQUIRY_WITH_RSSI_MODE = 0x01
HCI_EXTENDED_INQUIRY_MODE  = 0x02

# Page Scan Repetition Mode
HCI_R0_PAGE_SCAN_REPETITION_MODE = 0x00
HCI_R1_PAGE_SCAN_REPETITION_MODE = 0x01
HCI_R2_PAGE_SCAN_REPETITION_MODE = 0x02

# IO Capability
class IoCapability(SpecableEnum):
    DISPLAY_ONLY       = 0x00
    DISPLAY_YES_NO     = 0x01
    KEYBOARD_ONLY      = 0x02
    NO_INPUT_NO_OUTPUT = 0x03

# Authentication Requirements
class AuthenticationRequirements(SpecableEnum):
    MITM_NOT_REQUIRED_NO_BONDING        = 0x00
    MITM_REQUIRED_NO_BONDING            = 0x01
    MITM_NOT_REQUIRED_DEDICATED_BONDING = 0x02
    MITM_REQUIRED_DEDICATED_BONDING     = 0x03
    MITM_NOT_REQUIRED_GENERAL_BONDING   = 0x04
    MITM_REQUIRED_GENERAL_BONDING       = 0x05

# Link Key Types
class LinkKeyType(SpecableEnum):
    COMBINATION_KEY                                      = 0X00
    LOCAL_UNIT_KEY                                       = 0X01
    REMOTE_UNIT_KEY                                      = 0X02
    DEBUG_COMBINATION_KEY                                = 0X03
    UNAUTHENTICATED_COMBINATION_KEY_GENERATED_FROM_P_192 = 0X04
    AUTHENTICATED_COMBINATION_KEY_GENERATED_FROM_P_192   = 0X05
    CHANGED_COMBINATION_KEY                              = 0X06
    UNAUTHENTICATED_COMBINATION_KEY_GENERATED_FROM_P_256 = 0X07
    AUTHENTICATED_COMBINATION_KEY_GENERATED_FROM_P_256   = 0X08

# Address types
class AddressType(SpecableEnum):
    PUBLIC_DEVICE   = 0x00
    RANDOM_DEVICE   = 0x01
    PUBLIC_IDENTITY = 0x02
    RANDOM_IDENTITY = 0x03
    # (Directed Only) Address is RPA, but controller cannot resolve.
    UNABLE_TO_RESOLVE = 0xFE
    # (Extended Only) No address.
    ANONYMOUS       = 0xFF

# Supported Commands Masks
# See Bluetooth spec @ 6.27 SUPPORTED COMMANDS
HCI_SUPPORTED_COMMANDS_MASKS = {
    HCI_INQUIRY_COMMAND                                                       : 1 << (0*8+0),
    HCI_INQUIRY_CANCEL_COMMAND                                                : 1 << (0*8+1),
    HCI_PERIODIC_INQUIRY_MODE_COMMAND                                         : 1 << (0*8+2),
    HCI_EXIT_PERIODIC_INQUIRY_MODE_COMMAND                                    : 1 << (0*8+3),
    HCI_CREATE_CONNECTION_COMMAND                                             : 1 << (0*8+4),
    HCI_DISCONNECT_COMMAND                                                    : 1 << (0*8+5),
    HCI_CREATE_CONNECTION_CANCEL_COMMAND                                      : 1 << (0*8+7),
    HCI_ACCEPT_CONNECTION_REQUEST_COMMAND                                     : 1 << (1*8+0),
    HCI_REJECT_CONNECTION_REQUEST_COMMAND                                     : 1 << (1*8+1),
    HCI_LINK_KEY_REQUEST_REPLY_COMMAND                                        : 1 << (1*8+2),
    HCI_LINK_KEY_REQUEST_NEGATIVE_REPLY_COMMAND                               : 1 << (1*8+3),
    HCI_PIN_CODE_REQUEST_REPLY_COMMAND                                        : 1 << (1*8+4),
    HCI_PIN_CODE_REQUEST_NEGATIVE_REPLY_COMMAND                               : 1 << (1*8+5),
    HCI_CHANGE_CONNECTION_PACKET_TYPE_COMMAND                                 : 1 << (1*8+6),
    HCI_AUTHENTICATION_REQUESTED_COMMAND                                      : 1 << (1*8+7),
    HCI_SET_CONNECTION_ENCRYPTION_COMMAND                                     : 1 << (2*8+0),
    HCI_CHANGE_CONNECTION_LINK_KEY_COMMAND                                    : 1 << (2*8+1),
    HCI_LINK_KEY_SELECTION_COMMAND                                            : 1 << (2*8+2),
    HCI_REMOTE_NAME_REQUEST_COMMAND                                           : 1 << (2*8+3),
    HCI_REMOTE_NAME_REQUEST_CANCEL_COMMAND                                    : 1 << (2*8+4),
    HCI_READ_REMOTE_SUPPORTED_FEATURES_COMMAND                                : 1 << (2*8+5),
    HCI_READ_REMOTE_EXTENDED_FEATURES_COMMAND                                 : 1 << (2*8+6),
    HCI_READ_REMOTE_VERSION_INFORMATION_COMMAND                               : 1 << (2*8+7),
    HCI_READ_CLOCK_OFFSET_COMMAND                                             : 1 << (3*8+0),
    HCI_READ_LMP_HANDLE_COMMAND                                               : 1 << (3*8+1),
    HCI_HOLD_MODE_COMMAND                                                     : 1 << (4*8+1),
    HCI_SNIFF_MODE_COMMAND                                                    : 1 << (4*8+2),
    HCI_EXIT_SNIFF_MODE_COMMAND                                               : 1 << (4*8+3),
    HCI_QOS_SETUP_COMMAND                                                     : 1 << (4*8+6),
    HCI_ROLE_DISCOVERY_COMMAND                                                : 1 << (4*8+7),
    HCI_SWITCH_ROLE_COMMAND                                                   : 1 << (5*8+0),
    HCI_READ_LINK_POLICY_SETTINGS_COMMAND                                     : 1 << (5*8+1),
    HCI_WRITE_LINK_POLICY_SETTINGS_COMMAND                                    : 1 << (5*8+2),
    HCI_READ_DEFAULT_LINK_POLICY_SETTINGS_COMMAND                             : 1 << (5*8+3),
    HCI_WRITE_DEFAULT_LINK_POLICY_SETTINGS_COMMAND                            : 1 << (5*8+4),
    HCI_FLOW_SPECIFICATION_COMMAND                                            : 1 << (5*8+5),
    HCI_SET_EVENT_MASK_COMMAND                                                : 1 << (5*8+6),
    HCI_RESET_COMMAND                                                         : 1 << (5*8+7),
    HCI_SET_EVENT_FILTER_COMMAND                                              : 1 << (6*8+0),
    HCI_FLUSH_COMMAND                                                         : 1 << (6*8+1),
    HCI_READ_PIN_TYPE_COMMAND                                                 : 1 << (6*8+2),
    HCI_WRITE_PIN_TYPE_COMMAND                                                : 1 << (6*8+3),
    HCI_READ_STORED_LINK_KEY_COMMAND                                          : 1 << (6*8+5),
    HCI_WRITE_STORED_LINK_KEY_COMMAND                                         : 1 << (6*8+6),
    HCI_DELETE_STORED_LINK_KEY_COMMAND                                        : 1 << (6*8+7),
    HCI_WRITE_LOCAL_NAME_COMMAND                                              : 1 << (7*8+0),
    HCI_READ_LOCAL_NAME_COMMAND                                               : 1 << (7*8+1),
    HCI_READ_CONNECTION_ACCEPT_TIMEOUT_COMMAND                                : 1 << (7*8+2),
    HCI_WRITE_CONNECTION_ACCEPT_TIMEOUT_COMMAND                               : 1 << (7*8+3),
    HCI_READ_PAGE_TIMEOUT_COMMAND                                             : 1 << (7*8+4),
    HCI_WRITE_PAGE_TIMEOUT_COMMAND                                            : 1 << (7*8+5),
    HCI_READ_SCAN_ENABLE_COMMAND                                              : 1 << (7*8+6),
    HCI_WRITE_SCAN_ENABLE_COMMAND                                             : 1 << (7*8+7),
    HCI_READ_PAGE_SCAN_ACTIVITY_COMMAND                                       : 1 << (8*8+0),
    HCI_WRITE_PAGE_SCAN_ACTIVITY_COMMAND                                      : 1 << (8*8+1),
    HCI_READ_INQUIRY_SCAN_ACTIVITY_COMMAND                                    : 1 << (8*8+2),
    HCI_WRITE_INQUIRY_SCAN_ACTIVITY_COMMAND                                   : 1 << (8*8+3),
    HCI_READ_AUTHENTICATION_ENABLE_COMMAND                                    : 1 << (8*8+4),
    HCI_WRITE_AUTHENTICATION_ENABLE_COMMAND                                   : 1 << (8*8+5),
    HCI_READ_CLASS_OF_DEVICE_COMMAND                                          : 1 << (9*8+0),
    HCI_WRITE_CLASS_OF_DEVICE_COMMAND                                         : 1 << (9*8+1),
    HCI_READ_VOICE_SETTING_COMMAND                                            : 1 << (9*8+2),
    HCI_WRITE_VOICE_SETTING_COMMAND                                           : 1 << (9*8+3),
    HCI_READ_AUTOMATIC_FLUSH_TIMEOUT_COMMAND                                  : 1 << (9*8+4),
    HCI_WRITE_AUTOMATIC_FLUSH_TIMEOUT_COMMAND                                 : 1 << (9*8+5),
    HCI_READ_NUM_BROADCAST_RETRANSMISSIONS_COMMAND                            : 1 << (9*8+6),
    HCI_WRITE_NUM_BROADCAST_RETRANSMISSIONS_COMMAND                           : 1 << (9*8+7),
    HCI_READ_HOLD_MODE_ACTIVITY_COMMAND                                       : 1 << (10*8+0),
    HCI_WRITE_HOLD_MODE_ACTIVITY_COMMAND                                      : 1 << (10*8+1),
    HCI_READ_TRANSMIT_POWER_LEVEL_COMMAND                                     : 1 << (10*8+2),
    HCI_READ_SYNCHRONOUS_FLOW_CONTROL_ENABLE_COMMAND                          : 1 << (10*8+3),
    HCI_WRITE_SYNCHRONOUS_FLOW_CONTROL_ENABLE_COMMAND                         : 1 << (10*8+4),
    HCI_SET_CONTROLLER_TO_HOST_FLOW_CONTROL_COMMAND                           : 1 << (10*8+5),
    HCI_HOST_BUFFER_SIZE_COMMAND                                              : 1 << (10*8+6),
    HCI_HOST_NUMBER_OF_COMPLETED_PACKETS_COMMAND                              : 1 << (10*8+7),
    HCI_READ_LINK_SUPERVISION_TIMEOUT_COMMAND                                 : 1 << (11*8+0),
    HCI_WRITE_LINK_SUPERVISION_TIMEOUT_COMMAND                                : 1 << (11*8+1),
    HCI_READ_NUMBER_OF_SUPPORTED_IAC_COMMAND                                  : 1 << (11*8+2),
    HCI_READ_CURRENT_IAC_LAP_COMMAND                                          : 1 << (11*8+3),
    HCI_WRITE_CURRENT_IAC_LAP_COMMAND                                         : 1 << (11*8+4),
    HCI_SET_AFH_HOST_CHANNEL_CLASSIFICATION_COMMAND                           : 1 << (12*8+1),
    HCI_LE_CS_READ_REMOTE_FAE_TABLE_COMMAND                                   : 1 << (12*8+2),
    HCI_LE_CS_WRITE_CACHED_REMOTE_FAE_TABLE_COMMAND                           : 1 << (12*8+3),
    HCI_READ_INQUIRY_SCAN_TYPE_COMMAND                                        : 1 << (12*8+4),
    HCI_WRITE_INQUIRY_SCAN_TYPE_COMMAND                                       : 1 << (12*8+5),
    HCI_READ_INQUIRY_MODE_COMMAND                                             : 1 << (12*8+6),
    HCI_WRITE_INQUIRY_MODE_COMMAND                                            : 1 << (12*8+7),
    HCI_READ_PAGE_SCAN_TYPE_COMMAND                                           : 1 << (13*8+0),
    HCI_WRITE_PAGE_SCAN_TYPE_COMMAND                                          : 1 << (13*8+1),
    HCI_READ_AFH_CHANNEL_ASSESSMENT_MODE_COMMAND                              : 1 << (13*8+2),
    HCI_WRITE_AFH_CHANNEL_ASSESSMENT_MODE_COMMAND                             : 1 << (13*8+3),
    HCI_READ_LOCAL_VERSION_INFORMATION_COMMAND                                : 1 << (14*8+3),
    HCI_READ_LOCAL_SUPPORTED_FEATURES_COMMAND                                 : 1 << (14*8+5),
    HCI_READ_LOCAL_EXTENDED_FEATURES_COMMAND                                  : 1 << (14*8+6),
    HCI_READ_BUFFER_SIZE_COMMAND                                              : 1 << (14*8+7),
    HCI_READ_BD_ADDR_COMMAND                                                  : 1 << (15*8+1),
    HCI_READ_FAILED_CONTACT_COUNTER_COMMAND                                   : 1 << (15*8+2),
    HCI_RESET_FAILED_CONTACT_COUNTER_COMMAND                                  : 1 << (15*8+3),
    HCI_READ_LINK_QUALITY_COMMAND                                             : 1 << (15*8+4),
    HCI_READ_RSSI_COMMAND                                                     : 1 << (15*8+5),
    HCI_READ_AFH_CHANNEL_MAP_COMMAND                                          : 1 << (15*8+6),
    HCI_READ_CLOCK_COMMAND                                                    : 1 << (15*8+7),
    HCI_READ_LOOPBACK_MODE_COMMAND                                            : 1 << (16*8+0),
    HCI_WRITE_LOOPBACK_MODE_COMMAND                                           : 1 << (16*8+1),
    HCI_ENABLE_DEVICE_UNDER_TEST_MODE_COMMAND                                 : 1 << (16*8+2),
    HCI_SETUP_SYNCHRONOUS_CONNECTION_COMMAND                                  : 1 << (16*8+3),
    HCI_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND                         : 1 << (16*8+4),
    HCI_REJECT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND                         : 1 << (16*8+5),
    HCI_LE_CS_CREATE_CONFIG_COMMAND                                           : 1 << (16*8+6),
    HCI_LE_CS_REMOVE_CONFIG_COMMAND                                           : 1 << (16*8+7),
    HCI_READ_EXTENDED_INQUIRY_RESPONSE_COMMAND                                : 1 << (17*8+0),
    HCI_WRITE_EXTENDED_INQUIRY_RESPONSE_COMMAND                               : 1 << (17*8+1),
    HCI_REFRESH_ENCRYPTION_KEY_COMMAND                                        : 1 << (17*8+2),
    HCI_SNIFF_SUBRATING_COMMAND                                               : 1 << (17*8+4),
    HCI_READ_SIMPLE_PAIRING_MODE_COMMAND                                      : 1 << (17*8+5),
    HCI_WRITE_SIMPLE_PAIRING_MODE_COMMAND                                     : 1 << (17*8+6),
    HCI_READ_LOCAL_OOB_DATA_COMMAND                                           : 1 << (17*8+7),
    HCI_READ_INQUIRY_RESPONSE_TRANSMIT_POWER_LEVEL_COMMAND                    : 1 << (18*8+0),
    HCI_WRITE_INQUIRY_TRANSMIT_POWER_LEVEL_COMMAND                            : 1 << (18*8+1),
    HCI_READ_DEFAULT_ERRONEOUS_DATA_REPORTING_COMMAND                         : 1 << (18*8+2),
    HCI_WRITE_DEFAULT_ERRONEOUS_DATA_REPORTING_COMMAND                        : 1 << (18*8+3),
    HCI_IO_CAPABILITY_REQUEST_REPLY_COMMAND                                   : 1 << (18*8+7),
    HCI_USER_CONFIRMATION_REQUEST_REPLY_COMMAND                               : 1 << (19*8+0),
    HCI_USER_CONFIRMATION_REQUEST_NEGATIVE_REPLY_COMMAND                      : 1 << (19*8+1),
    HCI_USER_PASSKEY_REQUEST_REPLY_COMMAND                                    : 1 << (19*8+2),
    HCI_USER_PASSKEY_REQUEST_NEGATIVE_REPLY_COMMAND                           : 1 << (19*8+3),
    HCI_REMOTE_OOB_DATA_REQUEST_REPLY_COMMAND                                 : 1 << (19*8+4),
    HCI_WRITE_SIMPLE_PAIRING_DEBUG_MODE_COMMAND                               : 1 << (19*8+5),
    HCI_ENHANCED_FLUSH_COMMAND                                                : 1 << (19*8+6),
    HCI_REMOTE_OOB_DATA_REQUEST_NEGATIVE_REPLY_COMMAND                        : 1 << (19*8+7),
    HCI_SEND_KEYPRESS_NOTIFICATION_COMMAND                                    : 1 << (20*8+2),
    HCI_IO_CAPABILITY_REQUEST_NEGATIVE_REPLY_COMMAND                          : 1 << (20*8+3),
    HCI_READ_ENCRYPTION_KEY_SIZE_COMMAND                                      : 1 << (20*8+4),
    HCI_LE_CS_READ_LOCAL_SUPPORTED_CAPABILITIES_COMMAND                       : 1 << (20*8+5),
    HCI_LE_CS_READ_REMOTE_SUPPORTED_CAPABILITIES_COMMAND                      : 1 << (20*8+6),
    HCI_LE_CS_WRITE_CACHED_REMOTE_SUPPORTED_CAPABILITIES_COMMAND              : 1 << (20*8+7),
    HCI_SET_EVENT_MASK_PAGE_2_COMMAND                                         : 1 << (22*8+2),
    HCI_READ_FLOW_CONTROL_MODE_COMMAND                                        : 1 << (23*8+0),
    HCI_WRITE_FLOW_CONTROL_MODE_COMMAND                                       : 1 << (23*8+1),
    HCI_READ_DATA_BLOCK_SIZE_COMMAND                                          : 1 << (23*8+2),
    HCI_LE_CS_TEST_COMMAND                                                    : 1 << (23*8+3),
    HCI_LE_CS_TEST_END_COMMAND                                                : 1 << (23*8+4),
    HCI_READ_ENHANCED_TRANSMIT_POWER_LEVEL_COMMAND                            : 1 << (24*8+0),
    HCI_LE_CS_SECURITY_ENABLE_COMMAND                                         : 1 << (24*8+1),
    HCI_READ_LE_HOST_SUPPORT_COMMAND                                          : 1 << (24*8+5),
    HCI_WRITE_LE_HOST_SUPPORT_COMMAND                                         : 1 << (24*8+6),
    HCI_LE_CS_SET_DEFAULT_SETTINGS_COMMAND                                    : 1 << (24*8+7),
    HCI_LE_SET_EVENT_MASK_COMMAND                                             : 1 << (25*8+0),
    HCI_LE_READ_BUFFER_SIZE_COMMAND                                           : 1 << (25*8+1),
    HCI_LE_READ_LOCAL_SUPPORTED_FEATURES_COMMAND                              : 1 << (25*8+2),
    HCI_LE_SET_RANDOM_ADDRESS_COMMAND                                         : 1 << (25*8+4),
    HCI_LE_SET_ADVERTISING_PARAMETERS_COMMAND                                 : 1 << (25*8+5),
    HCI_LE_READ_ADVERTISING_PHYSICAL_CHANNEL_TX_POWER_COMMAND                 : 1 << (25*8+6),
    HCI_LE_SET_ADVERTISING_DATA_COMMAND                                       : 1 << (25*8+7),
    HCI_LE_SET_SCAN_RESPONSE_DATA_COMMAND                                     : 1 << (26*8+0),
    HCI_LE_SET_ADVERTISING_ENABLE_COMMAND                                     : 1 << (26*8+1),
    HCI_LE_SET_SCAN_PARAMETERS_COMMAND                                        : 1 << (26*8+2),
    HCI_LE_SET_SCAN_ENABLE_COMMAND                                            : 1 << (26*8+3),
    HCI_LE_CREATE_CONNECTION_COMMAND                                          : 1 << (26*8+4),
    HCI_LE_CREATE_CONNECTION_CANCEL_COMMAND                                   : 1 << (26*8+5),
    HCI_LE_READ_FILTER_ACCEPT_LIST_SIZE_COMMAND                               : 1 << (26*8+6),
    HCI_LE_CLEAR_FILTER_ACCEPT_LIST_COMMAND                                   : 1 << (26*8+7),
    HCI_LE_ADD_DEVICE_TO_FILTER_ACCEPT_LIST_COMMAND                           : 1 << (27*8+0),
    HCI_LE_REMOVE_DEVICE_FROM_FILTER_ACCEPT_LIST_COMMAND                      : 1 << (27*8+1),
    HCI_LE_CONNECTION_UPDATE_COMMAND                                          : 1 << (27*8+2),
    HCI_LE_SET_HOST_CHANNEL_CLASSIFICATION_COMMAND                            : 1 << (27*8+3),
    HCI_LE_READ_CHANNEL_MAP_COMMAND                                           : 1 << (27*8+4),
    HCI_LE_READ_REMOTE_FEATURES_COMMAND                                       : 1 << (27*8+5),
    HCI_LE_ENCRYPT_COMMAND                                                    : 1 << (27*8+6),
    HCI_LE_RAND_COMMAND                                                       : 1 << (27*8+7),
    HCI_LE_ENABLE_ENCRYPTION_COMMAND                                          : 1 << (28*8+0),
    HCI_LE_LONG_TERM_KEY_REQUEST_REPLY_COMMAND                                : 1 << (28*8+1),
    HCI_LE_LONG_TERM_KEY_REQUEST_NEGATIVE_REPLY_COMMAND                       : 1 << (28*8+2),
    HCI_LE_READ_SUPPORTED_STATES_COMMAND                                      : 1 << (28*8+3),
    HCI_LE_RECEIVER_TEST_COMMAND                                              : 1 << (28*8+4),
    HCI_LE_TRANSMITTER_TEST_COMMAND                                           : 1 << (28*8+5),
    HCI_LE_TEST_END_COMMAND                                                   : 1 << (28*8+6),
    HCI_LE_ENABLE_MONITORING_ADVERTISERS_COMMAND                              : 1 << (28*8+7),
    HCI_LE_CS_SET_CHANNEL_CLASSIFICATION_COMMAND                              : 1 << (29*8+0),
    HCI_LE_CS_SET_PROCEDURE_PARAMETERS_COMMAND                                : 1 << (29*8+1),
    HCI_LE_CS_PROCEDURE_ENABLE_COMMAND                                        : 1 << (29*8+2),
    HCI_ENHANCED_SETUP_SYNCHRONOUS_CONNECTION_COMMAND                         : 1 << (29*8+3),
    HCI_ENHANCED_ACCEPT_SYNCHRONOUS_CONNECTION_REQUEST_COMMAND                : 1 << (29*8+4),
    HCI_READ_LOCAL_SUPPORTED_CODECS_COMMAND                                   : 1 << (29*8+5),
    HCI_SET_MWS_CHANNEL_PARAMETERS_COMMAND                                    : 1 << (29*8+6),
    HCI_SET_EXTERNAL_FRAME_CONFIGURATION_COMMAND                              : 1 << (29*8+7),
    HCI_SET_MWS_SIGNALING_COMMAND                                             : 1 << (30*8+0),
    HCI_SET_MWS_TRANSPORT_LAYER_COMMAND                                       : 1 << (30*8+1),
    HCI_SET_MWS_SCAN_FREQUENCY_TABLE_COMMAND                                  : 1 << (30*8+2),
    HCI_GET_MWS_TRANSPORT_LAYER_CONFIGURATION_COMMAND                         : 1 << (30*8+3),
    HCI_SET_MWS_PATTERN_CONFIGURATION_COMMAND                                 : 1 << (30*8+4),
    HCI_SET_TRIGGERED_CLOCK_CAPTURE_COMMAND                                   : 1 << (30*8+5),
    HCI_TRUNCATED_PAGE_COMMAND                                                : 1 << (30*8+6),
    HCI_TRUNCATED_PAGE_CANCEL_COMMAND                                         : 1 << (30*8+7),
    HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_COMMAND                       : 1 << (31*8+0),
    HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVE_COMMAND               : 1 << (31*8+1),
    HCI_START_SYNCHRONIZATION_TRAIN_COMMAND                                   : 1 << (31*8+2),
    HCI_RECEIVE_SYNCHRONIZATION_TRAIN_COMMAND                                 : 1 << (31*8+3),
    HCI_SET_RESERVED_LT_ADDR_COMMAND                                          : 1 << (31*8+4),
    HCI_DELETE_RESERVED_LT_ADDR_COMMAND                                       : 1 << (31*8+5),
    HCI_SET_CONNECTIONLESS_PERIPHERAL_BROADCAST_DATA_COMMAND                  : 1 << (31*8+6),
    HCI_READ_SYNCHRONIZATION_TRAIN_PARAMETERS_COMMAND                         : 1 << (31*8+7),
    HCI_WRITE_SYNCHRONIZATION_TRAIN_PARAMETERS_COMMAND                        : 1 << (32*8+0),
    HCI_REMOTE_OOB_EXTENDED_DATA_REQUEST_REPLY_COMMAND                        : 1 << (32*8+1),
    HCI_READ_SECURE_CONNECTIONS_HOST_SUPPORT_COMMAND                          : 1 << (32*8+2),
    HCI_WRITE_SECURE_CONNECTIONS_HOST_SUPPORT_COMMAND                         : 1 << (32*8+3),
    HCI_READ_AUTHENTICATED_PAYLOAD_TIMEOUT_COMMAND                            : 1 << (32*8+4),
    HCI_WRITE_AUTHENTICATED_PAYLOAD_TIMEOUT_COMMAND                           : 1 << (32*8+5),
    HCI_READ_LOCAL_OOB_EXTENDED_DATA_COMMAND                                  : 1 << (32*8+6),
    HCI_WRITE_SECURE_CONNECTIONS_TEST_MODE_COMMAND                            : 1 << (32*8+7),
    HCI_READ_EXTENDED_PAGE_TIMEOUT_COMMAND                                    : 1 << (33*8+0),
    HCI_WRITE_EXTENDED_PAGE_TIMEOUT_COMMAND                                   : 1 << (33*8+1),
    HCI_READ_EXTENDED_INQUIRY_LENGTH_COMMAND                                  : 1 << (33*8+2),
    HCI_WRITE_EXTENDED_INQUIRY_LENGTH_COMMAND                                 : 1 << (33*8+3),
    HCI_LE_REMOTE_CONNECTION_PARAMETER_REQUEST_REPLY_COMMAND                  : 1 << (33*8+4),
    HCI_LE_REMOTE_CONNECTION_PARAMETER_REQUEST_NEGATIVE_REPLY_COMMAND         : 1 << (33*8+5),
    HCI_LE_SET_DATA_LENGTH_COMMAND                                            : 1 << (33*8+6),
    HCI_LE_READ_SUGGESTED_DEFAULT_DATA_LENGTH_COMMAND                         : 1 << (33*8+7),
    HCI_LE_WRITE_SUGGESTED_DEFAULT_DATA_LENGTH_COMMAND                        : 1 << (34*8+0),
    HCI_LE_READ_LOCAL_P_256_PUBLIC_KEY_COMMAND                                : 1 << (34*8+1),
    HCI_LE_GENERATE_DHKEY_COMMAND                                             : 1 << (34*8+2),
    HCI_LE_ADD_DEVICE_TO_RESOLVING_LIST_COMMAND                               : 1 << (34*8+3),
    HCI_LE_REMOVE_DEVICE_FROM_RESOLVING_LIST_COMMAND                          : 1 << (34*8+4),
    HCI_LE_CLEAR_RESOLVING_LIST_COMMAND                                       : 1 << (34*8+5),
    HCI_LE_READ_RESOLVING_LIST_SIZE_COMMAND                                   : 1 << (34*8+6),
    HCI_LE_READ_PEER_RESOLVABLE_ADDRESS_COMMAND                               : 1 << (34*8+7),
    HCI_LE_READ_LOCAL_RESOLVABLE_ADDRESS_COMMAND                              : 1 << (35*8+0),
    HCI_LE_SET_ADDRESS_RESOLUTION_ENABLE_COMMAND                              : 1 << (35*8+1),
    HCI_LE_SET_RESOLVABLE_PRIVATE_ADDRESS_TIMEOUT_COMMAND                     : 1 << (35*8+2),
    HCI_LE_READ_MAXIMUM_DATA_LENGTH_COMMAND                                   : 1 << (35*8+3),
    HCI_LE_READ_PHY_COMMAND                                                   : 1 << (35*8+4),
    HCI_LE_SET_DEFAULT_PHY_COMMAND                                            : 1 << (35*8+5),
    HCI_LE_SET_PHY_COMMAND                                                    : 1 << (35*8+6),
    HCI_LE_RECEIVER_TEST_V2_COMMAND                                           : 1 << (35*8+7),
    HCI_LE_TRANSMITTER_TEST_V2_COMMAND                                        : 1 << (36*8+0),
    HCI_LE_SET_ADVERTISING_SET_RANDOM_ADDRESS_COMMAND                         : 1 << (36*8+1),
    HCI_LE_SET_EXTENDED_ADVERTISING_PARAMETERS_COMMAND                        : 1 << (36*8+2),
    HCI_LE_SET_EXTENDED_ADVERTISING_DATA_COMMAND                              : 1 << (36*8+3),
    HCI_LE_SET_EXTENDED_SCAN_RESPONSE_DATA_COMMAND                            : 1 << (36*8+4),
    HCI_LE_SET_EXTENDED_ADVERTISING_ENABLE_COMMAND                            : 1 << (36*8+5),
    HCI_LE_READ_MAXIMUM_ADVERTISING_DATA_LENGTH_COMMAND                       : 1 << (36*8+6),
    HCI_LE_READ_NUMBER_OF_SUPPORTED_ADVERTISING_SETS_COMMAND                  : 1 << (36*8+7),
    HCI_LE_REMOVE_ADVERTISING_SET_COMMAND                                     : 1 << (37*8+0),
    HCI_LE_CLEAR_ADVERTISING_SETS_COMMAND                                     : 1 << (37*8+1),
    HCI_LE_SET_PERIODIC_ADVERTISING_PARAMETERS_COMMAND                        : 1 << (37*8+2),
    HCI_LE_SET_PERIODIC_ADVERTISING_DATA_COMMAND                              : 1 << (37*8+3),
    HCI_LE_SET_PERIODIC_ADVERTISING_ENABLE_COMMAND                            : 1 << (37*8+4),
    HCI_LE_SET_EXTENDED_SCAN_PARAMETERS_COMMAND                               : 1 << (37*8+5),
    HCI_LE_SET_EXTENDED_SCAN_ENABLE_COMMAND                                   : 1 << (37*8+6),
    HCI_LE_EXTENDED_CREATE_CONNECTION_COMMAND                                 : 1 << (37*8+7),
    HCI_LE_PERIODIC_ADVERTISING_CREATE_SYNC_COMMAND                           : 1 << (38*8+0),
    HCI_LE_PERIODIC_ADVERTISING_CREATE_SYNC_CANCEL_COMMAND                    : 1 << (38*8+1),
    HCI_LE_PERIODIC_ADVERTISING_TERMINATE_SYNC_COMMAND                        : 1 << (38*8+2),
    HCI_LE_ADD_DEVICE_TO_PERIODIC_ADVERTISER_LIST_COMMAND                     : 1 << (38*8+3),
    HCI_LE_REMOVE_DEVICE_FROM_PERIODIC_ADVERTISER_LIST_COMMAND                : 1 << (38*8+4),
    HCI_LE_CLEAR_PERIODIC_ADVERTISER_LIST_COMMAND                             : 1 << (38*8+5),
    HCI_LE_READ_PERIODIC_ADVERTISER_LIST_SIZE_COMMAND                         : 1 << (38*8+6),
    HCI_LE_READ_TRANSMIT_POWER_COMMAND                                        : 1 << (38*8+7),
    HCI_LE_READ_RF_PATH_COMPENSATION_COMMAND                                  : 1 << (39*8+0),
    HCI_LE_WRITE_RF_PATH_COMPENSATION_COMMAND                                 : 1 << (39*8+1),
    HCI_LE_SET_PRIVACY_MODE_COMMAND                                           : 1 << (39*8+2),
    HCI_LE_RECEIVER_TEST_V3_COMMAND                                           : 1 << (39*8+3),
    HCI_LE_TRANSMITTER_TEST_V3_COMMAND                                        : 1 << (39*8+4),
    HCI_LE_SET_CONNECTIONLESS_CTE_TRANSMIT_PARAMETERS_COMMAND                 : 1 << (39*8+5),
    HCI_LE_SET_CONNECTIONLESS_CTE_TRANSMIT_ENABLE_COMMAND                     : 1 << (39*8+6),
    HCI_LE_SET_CONNECTIONLESS_IQ_SAMPLING_ENABLE_COMMAND                      : 1 << (39*8+7),
    HCI_LE_SET_CONNECTION_CTE_RECEIVE_PARAMETERS_COMMAND                      : 1 << (40*8+0),
    HCI_LE_SET_CONNECTION_CTE_TRANSMIT_PARAMETERS_COMMAND                     : 1 << (40*8+1),
    HCI_LE_CONNECTION_CTE_REQUEST_ENABLE_COMMAND                              : 1 << (40*8+2),
    HCI_LE_CONNECTION_CTE_RESPONSE_ENABLE_COMMAND                             : 1 << (40*8+3),
    HCI_LE_READ_ANTENNA_INFORMATION_COMMAND                                   : 1 << (40*8+4),
    HCI_LE_SET_PERIODIC_ADVERTISING_RECEIVE_ENABLE_COMMAND                    : 1 << (40*8+5),
    HCI_LE_PERIODIC_ADVERTISING_SYNC_TRANSFER_COMMAND                         : 1 << (40*8+6),
    HCI_LE_PERIODIC_ADVERTISING_SET_INFO_TRANSFER_COMMAND                     : 1 << (40*8+7),
    HCI_LE_SET_PERIODIC_ADVERTISING_SYNC_TRANSFER_PARAMETERS_COMMAND          : 1 << (41*8+0),
    HCI_LE_SET_DEFAULT_PERIODIC_ADVERTISING_SYNC_TRANSFER_PARAMETERS_COMMAND  : 1 << (41*8+1),
    HCI_LE_GENERATE_DHKEY_V2_COMMAND                                          : 1 << (41*8+2),
    HCI_READ_LOCAL_SIMPLE_PAIRING_OPTIONS_COMMAND                             : 1 << (41*8+3),
    HCI_LE_MODIFY_SLEEP_CLOCK_ACCURACY_COMMAND                                : 1 << (41*8+4),
    HCI_LE_READ_BUFFER_SIZE_V2_COMMAND                                        : 1 << (41*8+5),
    HCI_LE_READ_ISO_TX_SYNC_COMMAND                                           : 1 << (41*8+6),
    HCI_LE_SET_CIG_PARAMETERS_COMMAND                                         : 1 << (41*8+7),
    HCI_LE_SET_CIG_PARAMETERS_TEST_COMMAND                                    : 1 << (42*8+0),
    HCI_LE_CREATE_CIS_COMMAND                                                 : 1 << (42*8+1),
    HCI_LE_REMOVE_CIG_COMMAND                                                 : 1 << (42*8+2),
    HCI_LE_ACCEPT_CIS_REQUEST_COMMAND                                         : 1 << (42*8+3),
    HCI_LE_REJECT_CIS_REQUEST_COMMAND                                         : 1 << (42*8+4),
    HCI_LE_CREATE_BIG_COMMAND                                                 : 1 << (42*8+5),
    HCI_LE_CREATE_BIG_TEST_COMMAND                                            : 1 << (42*8+6),
    HCI_LE_TERMINATE_BIG_COMMAND                                              : 1 << (42*8+7),
    HCI_LE_BIG_CREATE_SYNC_COMMAND                                            : 1 << (43*8+0),
    HCI_LE_BIG_TERMINATE_SYNC_COMMAND                                         : 1 << (43*8+1),
    HCI_LE_REQUEST_PEER_SCA_COMMAND                                           : 1 << (43*8+2),
    HCI_LE_SETUP_ISO_DATA_PATH_COMMAND                                        : 1 << (43*8+3),
    HCI_LE_REMOVE_ISO_DATA_PATH_COMMAND                                       : 1 << (43*8+4),
    HCI_LE_ISO_TRANSMIT_TEST_COMMAND                                          : 1 << (43*8+5),
    HCI_LE_ISO_RECEIVE_TEST_COMMAND                                           : 1 << (43*8+6),
    HCI_LE_ISO_READ_TEST_COUNTERS_COMMAND                                     : 1 << (43*8+7),
    HCI_LE_ISO_TEST_END_COMMAND                                               : 1 << (44*8+0),
    HCI_LE_SET_HOST_FEATURE_COMMAND                                           : 1 << (44*8+1),
    HCI_LE_READ_ISO_LINK_QUALITY_COMMAND                                      : 1 << (44*8+2),
    HCI_LE_ENHANCED_READ_TRANSMIT_POWER_LEVEL_COMMAND                         : 1 << (44*8+3),
    HCI_LE_READ_REMOTE_TRANSMIT_POWER_LEVEL_COMMAND                           : 1 << (44*8+4),
    HCI_LE_SET_PATH_LOSS_REPORTING_PARAMETERS_COMMAND                         : 1 << (44*8+5),
    HCI_LE_SET_PATH_LOSS_REPORTING_ENABLE_COMMAND                             : 1 << (44*8+6),
    HCI_LE_SET_TRANSMIT_POWER_REPORTING_ENABLE_COMMAND                        : 1 << (44*8+7),
    HCI_LE_TRANSMITTER_TEST_V4_COMMAND                                        : 1 << (45*8+0),
    HCI_SET_ECOSYSTEM_BASE_INTERVAL_COMMAND                                   : 1 << (45*8+1),
    HCI_READ_LOCAL_SUPPORTED_CODECS_V2_COMMAND                                : 1 << (45*8+2),
    HCI_READ_LOCAL_SUPPORTED_CODEC_CAPABILITIES_COMMAND                       : 1 << (45*8+3),
    HCI_READ_LOCAL_SUPPORTED_CONTROLLER_DELAY_COMMAND                         : 1 << (45*8+4),
    HCI_CONFIGURE_DATA_PATH_COMMAND                                           : 1 << (45*8+5),
    HCI_LE_SET_DATA_RELATED_ADDRESS_CHANGES_COMMAND                           : 1 << (45*8+6),
    HCI_SET_MIN_ENCRYPTION_KEY_SIZE_COMMAND                                   : 1 << (45*8+7),
    HCI_LE_SET_DEFAULT_SUBRATE_COMMAND                                        : 1 << (46*8+0),
    HCI_LE_SUBRATE_REQUEST_COMMAND                                            : 1 << (46*8+1),
    HCI_LE_SET_EXTENDED_ADVERTISING_PARAMETERS_V2_COMMAND                     : 1 << (46*8+2),
    HCI_LE_SET_DECISION_DATA_COMMAND                                          : 1 << (46*8+3),
    HCI_LE_SET_DECISION_INSTRUCTIONS_COMMAND                                  : 1 << (46*8+4),
    HCI_LE_SET_PERIODIC_ADVERTISING_SUBEVENT_DATA_COMMAND                     : 1 << (46*8+5),
    HCI_LE_SET_PERIODIC_ADVERTISING_RESPONSE_DATA_COMMAND                     : 1 << (46*8+6),
    HCI_LE_SET_PERIODIC_SYNC_SUBEVENT_COMMAND                                 : 1 << (46*8+7),
    HCI_LE_EXTENDED_CREATE_CONNECTION_V2_COMMAND                              : 1 << (47*8+0),
    HCI_LE_SET_PERIODIC_ADVERTISING_PARAMETERS_V2_COMMAND                     : 1 << (47*8+1),
    HCI_LE_READ_ALL_LOCAL_SUPPORTED_FEATURES_COMMAND                          : 1 << (47*8+2),
    HCI_LE_READ_ALL_REMOTE_FEATURES_COMMAND                                   : 1 << (47*8+3),
    HCI_LE_SET_HOST_FEATURE_V2_COMMAND                                        : 1 << (47*8+4),
    HCI_LE_ADD_DEVICE_TO_MONITORED_ADVERTISERS_LIST_COMMAND                   : 1 << (47*8+5),
    HCI_LE_REMOVE_DEVICE_FROM_MONITORED_ADVERTISERS_LIST_COMMAND              : 1 << (47*8+6),
    HCI_LE_CLEAR_MONITORED_ADVERTISERS_LIST_COMMAND                           : 1 << (47*8+7),
    HCI_LE_READ_MONITORED_ADVERTISERS_LIST_SIZE_COMMAND                       : 1 << (48*8+0),
    HCI_LE_FRAME_SPACE_UPDATE_COMMAND                                         : 1 << (48*8+1),
}

# LE Supported Features
# See Bluetooth spec @ Vol 6, Part B, 4.6 FEATURE SUPPORT
class LeFeature(SpecableEnum):
    LE_ENCRYPTION                                  = 0
    CONNECTION_PARAMETERS_REQUEST_PROCEDURE        = 1
    EXTENDED_REJECT_INDICATION                     = 2
    PERIPHERAL_INITIATED_FEATURE_EXCHANGE          = 3
    LE_PING                                        = 4
    LE_DATA_PACKET_LENGTH_EXTENSION                = 5
    LL_PRIVACY                                     = 6
    EXTENDED_SCANNER_FILTER_POLICIES               = 7
    LE_2M_PHY                                      = 8
    STABLE_MODULATION_INDEX_TRANSMITTER            = 9
    STABLE_MODULATION_INDEX_RECEIVER               = 10
    LE_CODED_PHY                                   = 11
    LE_EXTENDED_ADVERTISING                        = 12
    LE_PERIODIC_ADVERTISING                        = 13
    CHANNEL_SELECTION_ALGORITHM_2                  = 14
    LE_POWER_CLASS_1                               = 15
    MINIMUM_NUMBER_OF_USED_CHANNELS_PROCEDURE      = 16
    CONNECTION_CTE_REQUEST                         = 17
    CONNECTION_CTE_RESPONSE                        = 18
    CONNECTIONLESS_CTE_TRANSMITTER                 = 19
    CONNECTIONLESS_CTR_RECEIVER                    = 20
    ANTENNA_SWITCHING_DURING_CTE_TRANSMISSION      = 21
    ANTENNA_SWITCHING_DURING_CTE_RECEPTION         = 22
    RECEIVING_CONSTANT_TONE_EXTENSIONS             = 23
    PERIODIC_ADVERTISING_SYNC_TRANSFER_SENDER      = 24
    PERIODIC_ADVERTISING_SYNC_TRANSFER_RECIPIENT   = 25
    SLEEP_CLOCK_ACCURACY_UPDATES                   = 26
    REMOTE_PUBLIC_KEY_VALIDATION                   = 27
    CONNECTED_ISOCHRONOUS_STREAM_CENTRAL           = 28
    CONNECTED_ISOCHRONOUS_STREAM_PERIPHERAL        = 29
    ISOCHRONOUS_BROADCASTER                        = 30
    SYNCHRONIZED_RECEIVER                          = 31
    CONNECTED_ISOCHRONOUS_STREAM                   = 32
    LE_POWER_CONTROL_REQUEST                       = 33
    LE_POWER_CONTROL_REQUEST_DUP                   = 34
    LE_PATH_LOSS_MONITORING                        = 35
    PERIODIC_ADVERTISING_ADI_SUPPORT               = 36
    CONNECTION_SUBRATING                           = 37
    CONNECTION_SUBRATING_HOST_SUPPORT              = 38
    CHANNEL_CLASSIFICATION                         = 39
    ADVERTISING_CODING_SELECTION                   = 40
    ADVERTISING_CODING_SELECTION_HOST_SUPPORT      = 41
    DECISION_BASED_ADVERTISING_FILTERING           = 42
    PERIODIC_ADVERTISING_WITH_RESPONSES_ADVERTISER = 43
    PERIODIC_ADVERTISING_WITH_RESPONSES_SCANNER    = 44
    UNSEGMENTED_FRAMED_MODE                        = 45
    CHANNEL_SOUNDING                               = 46
    CHANNEL_SOUNDING_HOST_SUPPORT                  = 47
    CHANNEL_SOUNDING_TONE_QUALITY_INDICATION       = 48
    LL_EXTENDED_FEATURE_SET                        = 63
    MONITORING_ADVERTISERS                         = 64
    FRAME_SPACE_UPDATE                             = 65

class LeFeatureMask(enum.IntFlag):
    LE_ENCRYPTION                                  = 1 << LeFeature.LE_ENCRYPTION
    CONNECTION_PARAMETERS_REQUEST_PROCEDURE        = 1 << LeFeature.CONNECTION_PARAMETERS_REQUEST_PROCEDURE
    EXTENDED_REJECT_INDICATION                     = 1 << LeFeature.EXTENDED_REJECT_INDICATION
    PERIPHERAL_INITIATED_FEATURE_EXCHANGE          = 1 << LeFeature.PERIPHERAL_INITIATED_FEATURE_EXCHANGE
    LE_PING                                        = 1 << LeFeature.LE_PING
    LE_DATA_PACKET_LENGTH_EXTENSION                = 1 << LeFeature.LE_DATA_PACKET_LENGTH_EXTENSION
    LL_PRIVACY                                     = 1 << LeFeature.LL_PRIVACY
    EXTENDED_SCANNER_FILTER_POLICIES               = 1 << LeFeature.EXTENDED_SCANNER_FILTER_POLICIES
    LE_2M_PHY                                      = 1 << LeFeature.LE_2M_PHY
    STABLE_MODULATION_INDEX_TRANSMITTER            = 1 << LeFeature.STABLE_MODULATION_INDEX_TRANSMITTER
    STABLE_MODULATION_INDEX_RECEIVER               = 1 << LeFeature.STABLE_MODULATION_INDEX_RECEIVER
    LE_CODED_PHY                                   = 1 << LeFeature.LE_CODED_PHY
    LE_EXTENDED_ADVERTISING                        = 1 << LeFeature.LE_EXTENDED_ADVERTISING
    LE_PERIODIC_ADVERTISING                        = 1 << LeFeature.LE_PERIODIC_ADVERTISING
    CHANNEL_SELECTION_ALGORITHM_2                  = 1 << LeFeature.CHANNEL_SELECTION_ALGORITHM_2
    LE_POWER_CLASS_1                               = 1 << LeFeature.LE_POWER_CLASS_1
    MINIMUM_NUMBER_OF_USED_CHANNELS_PROCEDURE      = 1 << LeFeature.MINIMUM_NUMBER_OF_USED_CHANNELS_PROCEDURE
    CONNECTION_CTE_REQUEST                         = 1 << LeFeature.CONNECTION_CTE_REQUEST
    CONNECTION_CTE_RESPONSE                        = 1 << LeFeature.CONNECTION_CTE_RESPONSE
    CONNECTIONLESS_CTE_TRANSMITTER                 = 1 << LeFeature.CONNECTIONLESS_CTE_TRANSMITTER
    CONNECTIONLESS_CTR_RECEIVER                    = 1 << LeFeature.CONNECTIONLESS_CTR_RECEIVER
    ANTENNA_SWITCHING_DURING_CTE_TRANSMISSION      = 1 << LeFeature.ANTENNA_SWITCHING_DURING_CTE_TRANSMISSION
    ANTENNA_SWITCHING_DURING_CTE_RECEPTION         = 1 << LeFeature.ANTENNA_SWITCHING_DURING_CTE_RECEPTION
    RECEIVING_CONSTANT_TONE_EXTENSIONS             = 1 << LeFeature.RECEIVING_CONSTANT_TONE_EXTENSIONS
    PERIODIC_ADVERTISING_SYNC_TRANSFER_SENDER      = 1 << LeFeature.PERIODIC_ADVERTISING_SYNC_TRANSFER_SENDER
    PERIODIC_ADVERTISING_SYNC_TRANSFER_RECIPIENT   = 1 << LeFeature.PERIODIC_ADVERTISING_SYNC_TRANSFER_RECIPIENT
    SLEEP_CLOCK_ACCURACY_UPDATES                   = 1 << LeFeature.SLEEP_CLOCK_ACCURACY_UPDATES
    REMOTE_PUBLIC_KEY_VALIDATION                   = 1 << LeFeature.REMOTE_PUBLIC_KEY_VALIDATION
    CONNECTED_ISOCHRONOUS_STREAM_CENTRAL           = 1 << LeFeature.CONNECTED_ISOCHRONOUS_STREAM_CENTRAL
    CONNECTED_ISOCHRONOUS_STREAM_PERIPHERAL        = 1 << LeFeature.CONNECTED_ISOCHRONOUS_STREAM_PERIPHERAL
    ISOCHRONOUS_BROADCASTER                        = 1 << LeFeature.ISOCHRONOUS_BROADCASTER
    SYNCHRONIZED_RECEIVER                          = 1 << LeFeature.SYNCHRONIZED_RECEIVER
    CONNECTED_ISOCHRONOUS_STREAM                   = 1 << LeFeature.CONNECTED_ISOCHRONOUS_STREAM
    LE_POWER_CONTROL_REQUEST                       = 1 << LeFeature.LE_POWER_CONTROL_REQUEST
    LE_POWER_CONTROL_REQUEST_DUP                   = 1 << LeFeature.LE_POWER_CONTROL_REQUEST_DUP
    LE_PATH_LOSS_MONITORING                        = 1 << LeFeature.LE_PATH_LOSS_MONITORING
    PERIODIC_ADVERTISING_ADI_SUPPORT               = 1 << LeFeature.PERIODIC_ADVERTISING_ADI_SUPPORT
    CONNECTION_SUBRATING                           = 1 << LeFeature.CONNECTION_SUBRATING
    CONNECTION_SUBRATING_HOST_SUPPORT              = 1 << LeFeature.CONNECTION_SUBRATING_HOST_SUPPORT
    CHANNEL_CLASSIFICATION                         = 1 << LeFeature.CHANNEL_CLASSIFICATION
    ADVERTISING_CODING_SELECTION                   = 1 << LeFeature.ADVERTISING_CODING_SELECTION
    ADVERTISING_CODING_SELECTION_HOST_SUPPORT      = 1 << LeFeature.ADVERTISING_CODING_SELECTION_HOST_SUPPORT
    DECISION_BASED_ADVERTISING_FILTERING           = 1 << LeFeature.DECISION_BASED_ADVERTISING_FILTERING
    PERIODIC_ADVERTISING_WITH_RESPONSES_ADVERTISER = 1 << LeFeature.PERIODIC_ADVERTISING_WITH_RESPONSES_ADVERTISER
    PERIODIC_ADVERTISING_WITH_RESPONSES_SCANNER    = 1 << LeFeature.PERIODIC_ADVERTISING_WITH_RESPONSES_SCANNER
    UNSEGMENTED_FRAMED_MODE                        = 1 << LeFeature.UNSEGMENTED_FRAMED_MODE
    CHANNEL_SOUNDING                               = 1 << LeFeature.CHANNEL_SOUNDING
    CHANNEL_SOUNDING_HOST_SUPPORT                  = 1 << LeFeature.CHANNEL_SOUNDING_HOST_SUPPORT
    CHANNEL_SOUNDING_TONE_QUALITY_INDICATION       = 1 << LeFeature.CHANNEL_SOUNDING_TONE_QUALITY_INDICATION
    LL_EXTENDED_FEATURE_SET                        = 1 << LeFeature.LL_EXTENDED_FEATURE_SET
    MONITORING_ADVERTISERS                         = 1 << LeFeature.MONITORING_ADVERTISERS
    FRAME_SPACE_UPDATE                             = 1 << LeFeature.FRAME_SPACE_UPDATE

class LmpFeature(SpecableEnum):
    # Page 0 (Legacy LMP features)
    LMP_3_SLOT_PACKETS                                           = 0
    LMP_5_SLOT_PACKETS                                           = 1
    ENCRYPTION                                                   = 2
    SLOT_OFFSET                                                  = 3
    TIMING_ACCURACY                                              = 4
    ROLE_SWITCH                                                  = 5
    HOLD_MODE                                                    = 6
    SNIFF_MODE                                                   = 7
    # PREVIOUSLY_USED                                            = 8
    POWER_CONTROL_REQUESTS                                       = 9
    CHANNEL_QUALITY_DRIVEN_DATA_RATE_CQDDR                       = 10
    SCO_LINK                                                     = 11
    HV2_PACKETS                                                  = 12
    HV3_PACKETS                                                  = 13
    U_LAW_LOG_SYNCHRONOUS_DATA                                   = 14
    A_LAW_LOG_SYNCHRONOUS_DATA                                   = 15
    CVSD_SYNCHRONOUS_DATA                                        = 16
    PAGING_PARAMETER_NEGOTIATION                                 = 17
    POWER_CONTROL                                                = 18
    TRANSPARENT_SYNCHRONOUS_DATA                                 = 19
    FLOW_CONTROL_LAG_LEAST_SIGNIFICANT_BIT                       = 20
    FLOW_CONTROL_LAG_MIDDLE_BIT                                  = 21
    FLOW_CONTROL_LAG_MOST_SIGNIFICANT_BIT                        = 22
    BROADCAST_ENCRYPTION                                         = 23
    # RESERVED_FOR_FUTURE_USE                                    = 24
    ENHANCED_DATA_RATE_ACL_2_MBPS_MODE                           = 25
    ENHANCED_DATA_RATE_ACL_3_MBPS_MODE                           = 26
    ENHANCED_INQUIRY_SCAN                                        = 27
    INTERLACED_INQUIRY_SCAN                                      = 28
    INTERLACED_PAGE_SCAN                                         = 29
    RSSI_WITH_INQUIRY_RESULTS                                    = 30
    EXTENDED_SCO_LINK_EV3_PACKETS                                = 31
    EV4_PACKETS                                                  = 32
    EV5_PACKETS                                                  = 33
    # RESERVED_FOR_FUTURE_USE                                    = 34
    AFH_CAPABLE_PERIPHERAL                                       = 35
    AFH_CLASSIFICATION_PERIPHERAL                                = 36
    BR_EDR_NOT_SUPPORTED                                         = 37
    LE_SUPPORTED_CONTROLLER                                      = 38
    LMP_3_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS                    = 39
    LMP_5_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS                    = 40
    SNIFF_SUBRATING                                              = 41
    PAUSE_ENCRYPTION                                             = 42
    AFH_CAPABLE_CENTRAL                                          = 43
    AFH_CLASSIFICATION_CENTRAL                                   = 44
    ENHANCED_DATA_RATE_ESCO_2_MBPS_MODE                          = 45
    ENHANCED_DATA_RATE_ESCO_3_MBPS_MODE                          = 46
    LMP_3_SLOT_ENHANCED_DATA_RATE_ESCO_PACKETS                   = 47
    EXTENDED_INQUIRY_RESPONSE                                    = 48
    SIMULTANEOUS_LE_AND_BR_EDR_TO_SAME_DEVICE_CAPABLE_CONTROLLER = 49
    # RESERVED_FOR_FUTURE_USE                                    = 50
    SECURE_SIMPLE_PAIRING_CONTROLLER_SUPPORT                     = 51
    ENCAPSULATED_PDU                                             = 52
    ERRONEOUS_DATA_REPORTING                                     = 53
    NON_FLUSHABLE_PACKET_BOUNDARY_FLAG                           = 54
    # RESERVED_FOR_FUTURE_USE                                    = 55
    HCI_LINK_SUPERVISION_TIMEOUT_CHANGED_EVENT                   = 56
    VARIABLE_INQUIRY_TX_POWER_LEVEL                              = 57
    ENHANCED_POWER_CONTROL                                       = 58
    # RESERVED_FOR_FUTURE_USE                                    = 59
    # RESERVED_FOR_FUTURE_USE                                    = 60
    # RESERVED_FOR_FUTURE_USE                                    = 61
    # RESERVED_FOR_FUTURE_USE                                    = 62
    EXTENDED_FEATURES                                            = 63

    # Page 1
    SECURE_SIMPLE_PAIRING_HOST_SUPPORT                           = 64
    LE_SUPPORTED_HOST                                            = 65
    # PREVIOUSLY_USED                                            = 66
    SECURE_CONNECTIONS_HOST_SUPPORT                              = 67

    # Page 2
    CONNECTIONLESS_PERIPHERAL_BROADCAST_TRANSMITTER_OPERATION    = 128
    CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVER_OPERATION       = 129
    SYNCHRONIZATION_TRAIN                                        = 130
    SYNCHRONIZATION_SCAN                                         = 131
    HCI_INQUIRY_RESPONSE_NOTIFICATION_EVENT                      = 132
    GENERALIZED_INTERLACED_SCAN                                  = 133
    COARSE_CLOCK_ADJUSTMENT                                      = 134
    RESERVED_FOR_FUTURE_USE                                      = 135
    SECURE_CONNECTIONS_CONTROLLER_SUPPORT                        = 136
    PING                                                         = 137
    SLOT_AVAILABILITY_MASK                                       = 138
    TRAIN_NUDGING                                                = 139

class LmpFeatureMask(enum.IntFlag):
    # Page 0 (Legacy LMP features)
    LMP_3_SLOT_PACKETS                                           = (1 << LmpFeature.LMP_3_SLOT_PACKETS)
    LMP_5_SLOT_PACKETS                                           = (1 << LmpFeature.LMP_5_SLOT_PACKETS)
    ENCRYPTION                                                   = (1 << LmpFeature.ENCRYPTION)
    SLOT_OFFSET                                                  = (1 << LmpFeature.SLOT_OFFSET)
    TIMING_ACCURACY                                              = (1 << LmpFeature.TIMING_ACCURACY)
    ROLE_SWITCH                                                  = (1 << LmpFeature.ROLE_SWITCH)
    HOLD_MODE                                                    = (1 << LmpFeature.HOLD_MODE)
    SNIFF_MODE                                                   = (1 << LmpFeature.SNIFF_MODE)
    # PREVIOUSLY_USED                                            = (1 << LmpFeature.PREVIOUSLY_USED)
    POWER_CONTROL_REQUESTS                                       = (1 << LmpFeature.POWER_CONTROL_REQUESTS)
    CHANNEL_QUALITY_DRIVEN_DATA_RATE_CQDDR                       = (1 << LmpFeature.CHANNEL_QUALITY_DRIVEN_DATA_RATE_CQDDR)
    SCO_LINK                                                     = (1 << LmpFeature.SCO_LINK)
    HV2_PACKETS                                                  = (1 << LmpFeature.HV2_PACKETS)
    HV3_PACKETS                                                  = (1 << LmpFeature.HV3_PACKETS)
    U_LAW_LOG_SYNCHRONOUS_DATA                                   = (1 << LmpFeature.U_LAW_LOG_SYNCHRONOUS_DATA)
    A_LAW_LOG_SYNCHRONOUS_DATA                                   = (1 << LmpFeature.A_LAW_LOG_SYNCHRONOUS_DATA)
    CVSD_SYNCHRONOUS_DATA                                        = (1 << LmpFeature.CVSD_SYNCHRONOUS_DATA)
    PAGING_PARAMETER_NEGOTIATION                                 = (1 << LmpFeature.PAGING_PARAMETER_NEGOTIATION)
    POWER_CONTROL                                                = (1 << LmpFeature.POWER_CONTROL)
    TRANSPARENT_SYNCHRONOUS_DATA                                 = (1 << LmpFeature.TRANSPARENT_SYNCHRONOUS_DATA)
    FLOW_CONTROL_LAG_LEAST_SIGNIFICANT_BIT                       = (1 << LmpFeature.FLOW_CONTROL_LAG_LEAST_SIGNIFICANT_BIT)
    FLOW_CONTROL_LAG_MIDDLE_BIT                                  = (1 << LmpFeature.FLOW_CONTROL_LAG_MIDDLE_BIT)
    FLOW_CONTROL_LAG_MOST_SIGNIFICANT_BIT                        = (1 << LmpFeature.FLOW_CONTROL_LAG_MOST_SIGNIFICANT_BIT)
    BROADCAST_ENCRYPTION                                         = (1 << LmpFeature.BROADCAST_ENCRYPTION)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    ENHANCED_DATA_RATE_ACL_2_MBPS_MODE                           = (1 << LmpFeature.ENHANCED_DATA_RATE_ACL_2_MBPS_MODE)
    ENHANCED_DATA_RATE_ACL_3_MBPS_MODE                           = (1 << LmpFeature.ENHANCED_DATA_RATE_ACL_3_MBPS_MODE)
    ENHANCED_INQUIRY_SCAN                                        = (1 << LmpFeature.ENHANCED_INQUIRY_SCAN)
    INTERLACED_INQUIRY_SCAN                                      = (1 << LmpFeature.INTERLACED_INQUIRY_SCAN)
    INTERLACED_PAGE_SCAN                                         = (1 << LmpFeature.INTERLACED_PAGE_SCAN)
    RSSI_WITH_INQUIRY_RESULTS                                    = (1 << LmpFeature.RSSI_WITH_INQUIRY_RESULTS)
    EXTENDED_SCO_LINK_EV3_PACKETS                                = (1 << LmpFeature.EXTENDED_SCO_LINK_EV3_PACKETS)
    EV4_PACKETS                                                  = (1 << LmpFeature.EV4_PACKETS)
    EV5_PACKETS                                                  = (1 << LmpFeature.EV5_PACKETS)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    AFH_CAPABLE_PERIPHERAL                                       = (1 << LmpFeature.AFH_CAPABLE_PERIPHERAL)
    AFH_CLASSIFICATION_PERIPHERAL                                = (1 << LmpFeature.AFH_CLASSIFICATION_PERIPHERAL)
    BR_EDR_NOT_SUPPORTED                                         = (1 << LmpFeature.BR_EDR_NOT_SUPPORTED)
    LE_SUPPORTED_CONTROLLER                                      = (1 << LmpFeature.LE_SUPPORTED_CONTROLLER)
    LMP_3_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS                    = (1 << LmpFeature.LMP_3_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS)
    LMP_5_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS                    = (1 << LmpFeature.LMP_5_SLOT_ENHANCED_DATA_RATE_ACL_PACKETS)
    SNIFF_SUBRATING                                              = (1 << LmpFeature.SNIFF_SUBRATING)
    PAUSE_ENCRYPTION                                             = (1 << LmpFeature.PAUSE_ENCRYPTION)
    AFH_CAPABLE_CENTRAL                                          = (1 << LmpFeature.AFH_CAPABLE_CENTRAL)
    AFH_CLASSIFICATION_CENTRAL                                   = (1 << LmpFeature.AFH_CLASSIFICATION_CENTRAL)
    ENHANCED_DATA_RATE_ESCO_2_MBPS_MODE                          = (1 << LmpFeature.ENHANCED_DATA_RATE_ESCO_2_MBPS_MODE)
    ENHANCED_DATA_RATE_ESCO_3_MBPS_MODE                          = (1 << LmpFeature.ENHANCED_DATA_RATE_ESCO_3_MBPS_MODE)
    LMP_3_SLOT_ENHANCED_DATA_RATE_ESCO_PACKETS                   = (1 << LmpFeature.LMP_3_SLOT_ENHANCED_DATA_RATE_ESCO_PACKETS)
    EXTENDED_INQUIRY_RESPONSE                                    = (1 << LmpFeature.EXTENDED_INQUIRY_RESPONSE)
    SIMULTANEOUS_LE_AND_BR_EDR_TO_SAME_DEVICE_CAPABLE_CONTROLLER = (1 << LmpFeature.SIMULTANEOUS_LE_AND_BR_EDR_TO_SAME_DEVICE_CAPABLE_CONTROLLER)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    SECURE_SIMPLE_PAIRING_CONTROLLER_SUPPORT                     = (1 << LmpFeature.SECURE_SIMPLE_PAIRING_CONTROLLER_SUPPORT)
    ENCAPSULATED_PDU                                             = (1 << LmpFeature.ENCAPSULATED_PDU)
    ERRONEOUS_DATA_REPORTING                                     = (1 << LmpFeature.ERRONEOUS_DATA_REPORTING)
    NON_FLUSHABLE_PACKET_BOUNDARY_FLAG                           = (1 << LmpFeature.NON_FLUSHABLE_PACKET_BOUNDARY_FLAG)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    HCI_LINK_SUPERVISION_TIMEOUT_CHANGED_EVENT                   = (1 << LmpFeature.HCI_LINK_SUPERVISION_TIMEOUT_CHANGED_EVENT)
    VARIABLE_INQUIRY_TX_POWER_LEVEL                              = (1 << LmpFeature.VARIABLE_INQUIRY_TX_POWER_LEVEL)
    ENHANCED_POWER_CONTROL                                       = (1 << LmpFeature.ENHANCED_POWER_CONTROL)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    # RESERVED_FOR_FUTURE_USE                                    = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    EXTENDED_FEATURES                                            = (1 << LmpFeature.EXTENDED_FEATURES)

    # Page 1
    SECURE_SIMPLE_PAIRING_HOST_SUPPORT                           = (1 << LmpFeature.SECURE_SIMPLE_PAIRING_HOST_SUPPORT)
    LE_SUPPORTED_HOST                                            = (1 << LmpFeature.LE_SUPPORTED_HOST)
    # PREVIOUSLY_USED                                            = (1 << LmpFeature.PREVIOUSLY_USED)
    SECURE_CONNECTIONS_HOST_SUPPORT                              = (1 << LmpFeature.SECURE_CONNECTIONS_HOST_SUPPORT)

    # Page 2
    CONNECTIONLESS_PERIPHERAL_BROADCAST_TRANSMITTER_OPERATION    = (1 << LmpFeature.CONNECTIONLESS_PERIPHERAL_BROADCAST_TRANSMITTER_OPERATION)
    CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVER_OPERATION       = (1 << LmpFeature.CONNECTIONLESS_PERIPHERAL_BROADCAST_RECEIVER_OPERATION)
    SYNCHRONIZATION_TRAIN                                        = (1 << LmpFeature.SYNCHRONIZATION_TRAIN)
    SYNCHRONIZATION_SCAN                                         = (1 << LmpFeature.SYNCHRONIZATION_SCAN)
    HCI_INQUIRY_RESPONSE_NOTIFICATION_EVENT                      = (1 << LmpFeature.HCI_INQUIRY_RESPONSE_NOTIFICATION_EVENT)
    GENERALIZED_INTERLACED_SCAN                                  = (1 << LmpFeature.GENERALIZED_INTERLACED_SCAN)
    COARSE_CLOCK_ADJUSTMENT                                      = (1 << LmpFeature.COARSE_CLOCK_ADJUSTMENT)
    RESERVED_FOR_FUTURE_USE                                      = (1 << LmpFeature.RESERVED_FOR_FUTURE_USE)
    SECURE_CONNECTIONS_CONTROLLER_SUPPORT                        = (1 << LmpFeature.SECURE_CONNECTIONS_CONTROLLER_SUPPORT)
    PING                                                         = (1 << LmpFeature.PING)
    SLOT_AVAILABILITY_MASK                                       = (1 << LmpFeature.SLOT_AVAILABILITY_MASK)
    TRAIN_NUDGING                                                = (1 << LmpFeature.TRAIN_NUDGING)


# fmt: on
# pylint: enable=line-too-long
# pylint: disable=invalid-name

# -----------------------------------------------------------------------------
# pylint: disable-next=unnecessary-lambda
STATUS_SPEC = {'size': 1, 'mapper': lambda x: HCI_Constant.status_name(x)}
CS_ROLE_SPEC = {'size': 1, 'mapper': lambda x: CsRole(x).name}
CS_ROLE_MASK_SPEC = {'size': 1, 'mapper': lambda x: CsRoleMask(x).name}
CS_SYNC_PHY_SPEC = {'size': 1, 'mapper': lambda x: CsSyncPhy(x).name}
CS_SYNC_PHY_SUPPORTED_SPEC = {'size': 1, 'mapper': lambda x: CsSyncPhySupported(x).name}
RTT_TYPE_SPEC = {'size': 1, 'mapper': lambda x: RttType(x).name}
CS_SNR_SPEC = {'size': 1, 'mapper': lambda x: CsSnr(x).name}
COD_SPEC = {'size': 3, 'mapper': map_class_of_device}


class CodecID(SpecableEnum):
    # fmt: off
    U_LOG           = 0x00
    A_LOG           = 0x01
    CVSD            = 0x02
    TRANSPARENT     = 0x03
    LINEAR_PCM      = 0x04
    MSBC            = 0x05
    LC3             = 0x06
    G729A           = 0x07
    VENDOR_SPECIFIC = 0xFF


@dataclasses.dataclass(frozen=True)
class CodingFormat:
    codec_id: CodecID
    company_id: int = 0
    vendor_specific_codec_id: int = 0

    @classmethod
    def parse_from_bytes(cls, data: bytes, offset: int) -> tuple[int, CodingFormat]:
        (codec_id, company_id, vendor_specific_codec_id) = struct.unpack_from(
            '<BHH', data, offset
        )
        return offset + 5, cls(
            codec_id=CodecID(codec_id),
            company_id=company_id,
            vendor_specific_codec_id=vendor_specific_codec_id,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> CodingFormat:
        return cls.parse_from_bytes(data, 0)[1]

    def __bytes__(self) -> bytes:
        return struct.pack(
            '<BHH', self.codec_id, self.company_id, self.vendor_specific_codec_id
        )


# -----------------------------------------------------------------------------
class HCI_Constant:
    @staticmethod
    def status_name(status):
        return HCI_ERROR_NAMES.get(status, f'0x{status:02X}')

    @staticmethod
    def error_name(status):
        return HCI_ERROR_NAMES.get(status, f'0x{status:02X}')

    @staticmethod
    def role_name(role: int) -> str:
        return Role(role).name

    @staticmethod
    def le_phy_name(phy):
        return HCI_LE_PHY_NAMES.get(phy, str(phy))

    @staticmethod
    def inquiry_lap_name(lap):
        return HCI_INQUIRY_LAP_NAMES.get(lap, f'0x{lap:06X}')

    @staticmethod
    def io_capability_name(io_capability: int) -> str:
        return IoCapability(io_capability).name

    @staticmethod
    def authentication_requirements_name(authentication_requirements: int) -> str:
        return AuthenticationRequirements(authentication_requirements).name

    @staticmethod
    def link_key_type_name(link_key_type: int) -> str:
        return LinkKeyType(link_key_type).name


# -----------------------------------------------------------------------------
class HCI_Error(ProtocolError):
    def __init__(self, error_code):
        super().__init__(
            error_code,
            error_namespace='hci',
            error_name=HCI_Constant.error_name(error_code),
        )


# -----------------------------------------------------------------------------
class HCI_StatusError(ProtocolError):
    def __init__(self, response):
        super().__init__(
            response.status,
            error_namespace=HCI_Command.command_name(response.command_opcode),
            error_name=HCI_Constant.status_name(response.status),
        )


# -----------------------------------------------------------------------------
# Generic HCI object
# -----------------------------------------------------------------------------
class HCI_Object:
    @staticmethod
    def init_from_fields(hci_object, fields, values):
        if isinstance(values, dict):
            for field in fields:
                if isinstance(field, list):
                    # The field is an array, up-level the array field names
                    for sub_field_name, _ in field:
                        setattr(hci_object, sub_field_name, values[sub_field_name])
                else:
                    field_name = field[0]
                    setattr(hci_object, field_name, values[field_name])
        else:
            for field_name, field_value in zip(fields, values):
                setattr(hci_object, field_name, field_value)

    @staticmethod
    def init_from_bytes(hci_object, data, offset, fields):
        parsed = HCI_Object.dict_from_bytes(data, offset, fields)
        HCI_Object.init_from_fields(hci_object, parsed.keys(), parsed.values())

    @staticmethod
    def parse_field(data: bytes, offset: int, field_type: FieldSpec):
        # The field_type may be a dictionary with a mapper, parser, and/or size
        if isinstance(field_type, dict):
            if 'size' in field_type:
                field_type = field_type['size']
            elif 'parser' in field_type:
                field_type = field_type['parser']

        # Parse the field
        if field_type == '*':
            # The rest of the bytes
            field_value = data[offset:]
            return (field_value, len(field_value))
        if field_type == 'v':
            # Variable-length bytes field, with 1-byte length at the beginning
            field_length = data[offset]
            offset += 1
            field_value = data[offset : offset + field_length]
            return (field_value, field_length + 1)
        if field_type == 1:
            # 8-bit unsigned
            return (data[offset], 1)
        if field_type == -1:
            # 8-bit signed
            return (struct.unpack_from('b', data, offset)[0], 1)
        if field_type == 2:
            # 16-bit unsigned
            return (struct.unpack_from('<H', data, offset)[0], 2)
        if field_type == '>2':
            # 16-bit unsigned big-endian
            return (struct.unpack_from('>H', data, offset)[0], 2)
        if field_type == -2:
            # 16-bit signed
            return (struct.unpack_from('<h', data, offset)[0], 2)
        if field_type == 3:
            # 24-bit unsigned
            padded = data[offset : offset + 3] + bytes([0])
            return (struct.unpack('<I', padded)[0], 3)
        if field_type == 4:
            # 32-bit unsigned
            return (struct.unpack_from('<I', data, offset)[0], 4)
        if field_type == '>4':
            # 32-bit unsigned big-endian
            return (struct.unpack_from('>I', data, offset)[0], 4)
        if isinstance(field_type, int) and 4 < field_type <= 256:
            # Byte array (from 5 up to 256 bytes)
            return (data[offset : offset + field_type], field_type)
        if callable(field_type):
            new_offset, field_value = field_type(data, offset)
            return (field_value, new_offset - offset)

        raise InvalidArgumentError(f'unknown field type {field_type}')

    @classmethod
    def dict_and_offset_from_bytes(
        cls, data: bytes, offset: int, fields: Fields
    ) -> tuple[int, collections.OrderedDict[str, Any]]:
        result = collections.OrderedDict[str, Any]()
        for field in fields:
            if isinstance(field, list):
                # This is an array field, starting with a 1-byte item count.
                item_count = data[offset]
                offset += 1
                # Set fields first, because item_count might be 0.
                for sub_field_name, _ in field:
                    result[sub_field_name] = []

                for _ in range(item_count):
                    for sub_field_name, sub_field_type in field:
                        value, size = HCI_Object.parse_field(
                            data, offset, sub_field_type
                        )
                        result[sub_field_name].append(value)
                        offset += size
                continue

            field_name, field_type = field
            assert isinstance(field_name, str)
            field_value, field_size = HCI_Object.parse_field(
                data, offset, cast(FieldSpec, field_type)
            )
            result[field_name] = field_value
            offset += field_size

        return (offset, result)

    @staticmethod
    def dict_from_bytes(data, offset, fields):
        return HCI_Object.dict_and_offset_from_bytes(data, offset, fields)[1]

    @staticmethod
    def serialize_field(field_value: Any, field_type: FieldSpec) -> bytes:
        # The field_type may be a dictionary with a mapper, parser, serializer,
        # and/or size
        serializer = None
        if isinstance(field_type, dict):
            if 'serializer' in field_type:
                serializer = field_type['serializer']
            if 'size' in field_type:
                field_type = field_type['size']

        # Serialize the field
        if serializer:
            field_bytes = serializer(field_value)
        elif field_type == 1:
            # 8-bit unsigned
            field_bytes = bytes([field_value])
        elif field_type == -1:
            # 8-bit signed
            field_bytes = struct.pack('b', field_value)
        elif field_type == 2:
            # 16-bit unsigned
            field_bytes = struct.pack('<H', field_value)
        elif field_type == '>2':
            # 16-bit unsigned big-endian
            field_bytes = struct.pack('>H', field_value)
        elif field_type == -2:
            # 16-bit signed
            field_bytes = struct.pack('<h', field_value)
        elif field_type == 3:
            # 24-bit unsigned
            field_bytes = struct.pack('<I', field_value)[0:3]
        elif field_type == 4:
            # 32-bit unsigned
            field_bytes = struct.pack('<I', field_value)
        elif field_type == '>4':
            # 32-bit unsigned big-endian
            field_bytes = struct.pack('>I', field_value)
        elif field_type == '*':
            if isinstance(field_value, int):
                if 0 <= field_value <= 255:
                    field_bytes = bytes([field_value])
                else:
                    raise InvalidArgumentError('value too large for *-typed field')
            else:
                field_bytes = bytes(field_value)
        elif field_type == 'v':
            # Variable-length bytes field, with 1-byte length at the beginning
            field_bytes = bytes(field_value)
            field_length = len(field_bytes)
            field_bytes = bytes([field_length]) + field_bytes
        elif isinstance(field_value, (bytes, bytearray)) or hasattr(
            field_value, '__bytes__'
        ):
            field_bytes = bytes(field_value)
            if isinstance(field_type, int) and 4 < field_type <= 256:
                # Truncate or pad with zeros if the field is too long or too short
                if len(field_bytes) < field_type:
                    field_bytes += bytes(field_type - len(field_bytes))
                elif len(field_bytes) > field_type:
                    field_bytes = field_bytes[:field_type]
        else:
            raise InvalidArgumentError(
                f"don't know how to serialize type {type(field_value)}"
            )

        return field_bytes

    @staticmethod
    def dict_to_bytes(hci_object, fields):
        result = bytearray()
        for field in fields:
            if isinstance(field, list):
                # The field is an array. The serialized form starts with a 1-byte
                # item count. We use the length of the first array field as the
                # array count, since all array fields have the same number of items.
                item_count = len(hci_object[field[0][0]])
                result += bytes([item_count]) + b''.join(
                    b''.join(
                        HCI_Object.serialize_field(
                            hci_object[sub_field_name][i], sub_field_type
                        )
                        for sub_field_name, sub_field_type in field
                    )
                    for i in range(item_count)
                )
                continue

            (field_name, field_type) = field
            result += HCI_Object.serialize_field(hci_object[field_name], field_type)

        return bytes(result)

    @classmethod
    def from_bytes(cls, data, offset, fields):
        return cls(fields, **cls.dict_from_bytes(data, offset, fields))

    def __bytes__(self):
        return HCI_Object.dict_to_bytes(self.__dict__, self.fields)

    @staticmethod
    def parse_length_prefixed_bytes(data, offset):
        length = data[offset]
        return offset + 1 + length, data[offset + 1 : offset + 1 + length]

    @staticmethod
    def serialize_length_prefixed_bytes(data, padded_size=0):
        prefixed_size = 1 + len(data)
        padding = (
            bytes(padded_size - prefixed_size) if prefixed_size < padded_size else b''
        )
        return bytes([len(data)]) + data + padding

    @staticmethod
    def format_field_value(value, indentation):
        if isinstance(value, bytes):
            return value.hex()

        if isinstance(value, HCI_Object):
            return '\n' + value.to_string(indentation)

        return str(value)

    @staticmethod
    def stringify_field(
        field_name, field_type, field_value, indentation, value_mappers
    ):
        value_mapper = None
        if isinstance(field_type, dict):
            # Get the value mapper from the specifier
            value_mapper = field_type.get('mapper')

        # Check if there's a matching mapper passed
        if value_mappers:
            value_mapper = value_mappers.get(field_name, value_mapper)

        # Map the value if we have a mapper
        if value_mapper is not None:
            field_value = value_mapper(field_value)

        # Get the string representation of the value
        return HCI_Object.format_field_value(
            field_value, indentation=indentation + '  '
        )

    @staticmethod
    def format_fields(hci_object, fields, indentation='', value_mappers=None):
        if not fields:
            return ''

        # Build array of formatted key:value pairs
        field_strings = []
        for field in fields:
            if isinstance(field, list):
                for sub_field in field:
                    sub_field_name, sub_field_type = sub_field
                    item_count = len(hci_object[sub_field_name])
                    for i in range(item_count):
                        field_strings.append(
                            (
                                f'{sub_field_name}[{i}]',
                                HCI_Object.stringify_field(
                                    sub_field_name,
                                    sub_field_type,
                                    hci_object[sub_field_name][i],
                                    indentation,
                                    value_mappers,
                                ),
                            ),
                        )
                continue

            field_name, field_type = field
            field_value = hci_object[field_name]
            field_strings.append(
                (
                    field_name,
                    HCI_Object.stringify_field(
                        field_name, field_type, field_value, indentation, value_mappers
                    ),
                ),
            )

        # Measure the widest field name
        max_field_name_length = max(len(s[0]) for s in field_strings)
        sep = ':'
        return '\n'.join(
            f'{indentation}'
            f'{color(f"{field_name + sep:{1 + max_field_name_length}}", "cyan")} {field_value}'
            for field_name, field_value in field_strings
        )

    @classmethod
    def fields_from_dataclass(cls, obj: Any) -> list[Any]:
        stack: list[list[Any]] = [[]]
        for field in dataclasses.fields(obj):
            # Fields without metadata should be ignored.
            if not isinstance(
                (metadata := field.metadata.get("bumble.hci")), FieldMetadata
            ):
                continue
            if metadata.list_begin:
                stack.append([])
            if metadata.spec:
                stack[-1].append((field.name, metadata.spec))
            if metadata.list_end:
                top = stack.pop()
                stack[-1].append(top)
        return stack[0]

    def __init__(self, fields, **kwargs):
        self.fields = fields
        self.init_from_fields(self, fields, kwargs)

    def to_string(self, indentation='', value_mappers=None):
        return HCI_Object.format_fields(
            self.__dict__, self.fields, indentation, value_mappers
        )

    def __str__(self):
        return self.to_string()


# -----------------------------------------------------------------------------
@dataclasses.dataclass
class HCI_Dataclass_Object(HCI_Object):
    def __post_init__(self) -> None:
        self.fields = HCI_Object.fields_from_dataclass(self)

    @classmethod
    def parse_from_bytes(cls, data: bytes, offset: int) -> tuple[int, Self]:
        fields = HCI_Object.fields_from_dataclass(cls)
        offset, kwargs = HCI_Object.dict_and_offset_from_bytes(data, offset, fields)
        return offset, cls(**kwargs)


# -----------------------------------------------------------------------------
# Bluetooth Address
# -----------------------------------------------------------------------------
class Address:
    '''
    Bluetooth Address (see Bluetooth spec Vol 6, Part B - 1.3 DEVICE ADDRESS)
    NOTE: the address bytes are stored in little-endian byte order here, so
    address[0] is the LSB of the address, address[5] is the MSB.
    '''

    PUBLIC_DEVICE_ADDRESS = AddressType.PUBLIC_DEVICE
    RANDOM_DEVICE_ADDRESS = AddressType.RANDOM_DEVICE
    PUBLIC_IDENTITY_ADDRESS = AddressType.PUBLIC_IDENTITY
    RANDOM_IDENTITY_ADDRESS = AddressType.RANDOM_IDENTITY

    # Type declarations
    NIL: Address
    ANY: Address
    ANY_RANDOM: Address

    # pylint: disable-next=unnecessary-lambda
    ADDRESS_TYPE_SPEC = {'size': 1, 'mapper': lambda x: Address.address_type_name(x)}

    @classmethod
    def address_type_name(cls: type[Self], address_type: int) -> str:
        return AddressType(address_type).name

    @classmethod
    def from_string_for_transport(
        cls: type[Self], string: str, transport: PhysicalTransport
    ) -> Self:
        if transport == PhysicalTransport.BR_EDR:
            address_type = Address.PUBLIC_DEVICE_ADDRESS
        else:
            address_type = Address.RANDOM_DEVICE_ADDRESS
        return cls(string, address_type)

    @classmethod
    def parse_address(cls: type[Self], data: bytes, offset: int) -> tuple[int, Self]:
        # Fix the type to a default value. This is used for parsing type-less Classic
        # addresses
        return cls.parse_address_with_type(data, offset, Address.PUBLIC_DEVICE_ADDRESS)

    @classmethod
    def parse_random_address(
        cls: type[Self], data: bytes, offset: int
    ) -> tuple[int, Self]:
        return cls.parse_address_with_type(data, offset, Address.RANDOM_DEVICE_ADDRESS)

    @classmethod
    def parse_address_with_type(
        cls: type[Self], data: bytes, offset: int, address_type: AddressType
    ) -> tuple[int, Self]:
        return offset + 6, cls(data[offset : offset + 6], address_type)

    @classmethod
    def parse_address_preceded_by_type(
        cls: type[Self], data: bytes, offset: int
    ) -> tuple[int, Self]:
        address_type = AddressType(data[offset - 1])
        return cls.parse_address_with_type(data, offset, address_type)

    @classmethod
    def generate_static_address(cls) -> Address:
        '''Generates Random Static Address, with the 2 most significant bits of 0b11.

        See Bluetooth spec, Vol 6, Part B - Table 1.2.
        '''
        address_bytes = secrets.token_bytes(6)
        address_bytes = address_bytes[:5] + bytes([address_bytes[5] | 0b11000000])
        return Address(
            address=address_bytes, address_type=Address.RANDOM_DEVICE_ADDRESS
        )

    @classmethod
    def generate_private_address(cls, irk: bytes = b'') -> Address:
        '''Generates Random Private MAC Address.

        If IRK is present, a Resolvable Private Address, with the 2 most significant
        bits of 0b01 will be generated. Otherwise, a Non-resolvable Private Address,
        with the 2 most significant bits of 0b00 will be generated.

        See Bluetooth spec, Vol 6, Part B - Table 1.2.

        Args:
            irk: Local Identity Resolving Key(IRK), in little-endian. If not set, a
            non-resolvable address will be generated.
        '''
        if irk:
            prand = crypto.generate_prand()
            address_bytes = crypto.ah(irk, prand) + prand
        else:
            address_bytes = secrets.token_bytes(6)
            address_bytes = address_bytes[:5] + bytes([address_bytes[5] & 0b00111111])

        return Address(
            address=address_bytes, address_type=Address.RANDOM_DEVICE_ADDRESS
        )

    def __init__(
        self,
        address: Union[bytes, str],
        address_type: AddressType = RANDOM_DEVICE_ADDRESS,
    ) -> None:
        '''
        Initialize an instance. `address` may be a byte array in little-endian
        format, or a hex string in big-endian format (with optional ':'
        separators between the bytes).
        If the address is a string suffixed with '/P', `address_type` is ignored and
        the type is set to PUBLIC_DEVICE_ADDRESS.
        '''
        if isinstance(address, bytes):
            self.address_bytes = address
        else:
            # Check if there's a '/P' type specifier
            if address.endswith('P'):
                address_type = Address.PUBLIC_DEVICE_ADDRESS
                address = address[:-2]

            if len(address) == 12 + 5:
                # Form with ':' separators
                address = address.replace(':', '')
            self.address_bytes = bytes(reversed(bytes.fromhex(address)))

        if len(self.address_bytes) != 6:
            raise InvalidArgumentError('invalid address length')

        self.address_type = address_type

    def clone(self):
        return Address(self.address_bytes, self.address_type)

    @property
    def is_public(self):
        return self.address_type in (
            self.PUBLIC_DEVICE_ADDRESS,
            self.PUBLIC_IDENTITY_ADDRESS,
        )

    @property
    def is_random(self):
        return not self.is_public

    @property
    def is_resolved(self):
        return self.address_type in (
            self.PUBLIC_IDENTITY_ADDRESS,
            self.RANDOM_IDENTITY_ADDRESS,
        )

    @property
    def is_resolvable(self):
        return self.address_type == self.RANDOM_DEVICE_ADDRESS and (
            self.address_bytes[5] >> 6 == 1
        )

    @property
    def is_static(self):
        return self.is_random and (self.address_bytes[5] >> 6 == 3)

    def to_string(self, with_type_qualifier=True):
        '''
        String representation of the address, MSB first, with an optional type
        qualifier.
        '''
        result = ':'.join([f'{x:02X}' for x in reversed(self.address_bytes)])
        if not with_type_qualifier or not self.is_public:
            return result
        return result + '/P'

    def __bytes__(self):
        return self.address_bytes

    def __hash__(self):
        return hash(self.address_bytes)

    def __eq__(self, other):
        return (
            isinstance(other, Address)
            and self.address_bytes == other.address_bytes
            and self.is_public == other.is_public
        )

    def __str__(self):
        return self.to_string()

    def __repr__(self):
        return f'Address({self.to_string(False)}/{self.address_type_name(self.address_type)})'


# Predefined address values
Address.NIL = Address(b"\xff\xff\xff\xff\xff\xff", Address.PUBLIC_DEVICE_ADDRESS)
Address.ANY = Address(b"\x00\x00\x00\x00\x00\x00", Address.PUBLIC_DEVICE_ADDRESS)
Address.ANY_RANDOM = Address(b"\x00\x00\x00\x00\x00\x00", Address.RANDOM_DEVICE_ADDRESS)


# -----------------------------------------------------------------------------
class OwnAddressType(SpecableEnum):
    PUBLIC = 0
    RANDOM = 1
    RESOLVABLE_OR_PUBLIC = 2
    RESOLVABLE_OR_RANDOM = 3


# -----------------------------------------------------------------------------
class LoopbackMode(SpecableEnum):
    DISABLED = 0
    LOCAL = 1
    REMOTE = 2


# -----------------------------------------------------------------------------
class CteType(SpecableEnum):
    AOA_CONSTANT_TONE_EXTENSION = 0x00
    AOD_CONSTANT_TONE_EXTENSION_1US = 0x01
    AOD_CONSTANT_TONE_EXTENSION_2US = 0x02
    NO_CONSTANT_TONE_EXTENSION = 0xFF


# -----------------------------------------------------------------------------
class HCI_Packet:
    '''
    Abstract Base class for HCI packets
    '''

    hci_packet_type: ClassVar[int]

    @staticmethod
    def from_bytes(packet: bytes) -> HCI_Packet:
        packet_type = packet[0]

        if packet_type == HCI_COMMAND_PACKET:
            return HCI_Command.from_bytes(packet)

        if packet_type == HCI_ACL_DATA_PACKET:
            return HCI_AclDataPacket.from_bytes(packet)

        if packet_type == HCI_SYNCHRONOUS_DATA_PACKET:
            return HCI_SynchronousDataPacket.from_bytes(packet)

        if packet_type == HCI_EVENT_PACKET:
            return HCI_Event.from_bytes(packet)

        if packet_type == HCI_ISO_DATA_PACKET:
            return HCI_IsoDataPacket.from_bytes(packet)

        return HCI_CustomPacket(packet)

    def __init__(self, name):
        self.name = name

    def __bytes__(self) -> bytes:
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.name


# -----------------------------------------------------------------------------
class HCI_CustomPacket(HCI_Packet):
    def __init__(self, payload):
        super().__init__('HCI_CUSTOM_PACKET')
        self.hci_packet_type = payload[0]
        self.payload = payload

    def __bytes__(self) -> bytes:
        return self.payload


# -----------------------------------------------------------------------------
class HCI_Command(HCI_Packet):
    '''
    See Bluetooth spec @ Vol 2, Part E - 5.4.1 HCI Command Packet
    '''

    hci_packet_type = HCI_COMMAND_PACKET
    command_names: dict[int, str] = {}
    command_classes: dict[int, type[HCI_Command]] = {}
    op_code: int
    fields: Fields = ()
    return_parameters_fields: Fields = ()
    _parameters: bytes = b''

    _Command = TypeVar("_Command", bound="HCI_Command")

    @classmethod
    def command(cls, subclass: type[_Command]) -> type[_Command]:
        '''
        Decorator used to declare and register subclasses
        '''

        # Subclasses may set parameters as ClassVar, or inferred from class name.
        if not hasattr(subclass, 'name'):
            subclass.name = subclass.__name__.upper()
        if not hasattr(subclass, 'op_code'):
            op_code = key_with_value(subclass.command_names, subclass.name)
            if op_code is None:
                raise KeyError(f'command {subclass.name} not found in command_names')
            subclass.op_code = op_code

        if dataclasses.is_dataclass(subclass):
            subclass.fields = HCI_Object.fields_from_dataclass(subclass)

        # Register a factory for this class
        cls.command_classes[subclass.op_code] = subclass

        return subclass

    @staticmethod
    def command_map(symbols: dict[str, Any]) -> dict[int, str]:
        return {
            command_code: command_name
            for (command_name, command_code) in symbols.items()
            if command_name.startswith('HCI_') and command_name.endswith('_COMMAND')
        }

    @classmethod
    def register_commands(cls, symbols: dict[str, Any]) -> None:
        cls.command_names.update(cls.command_map(symbols))

    @staticmethod
    def from_bytes(packet: bytes) -> HCI_Command:
        op_code, length = struct.unpack_from('<HB', packet, 1)
        parameters = packet[4:]
        if len(parameters) != length:
            raise InvalidPacketError('invalid packet length')

        # Look for a registered class
        cls = HCI_Command.command_classes.get(op_code)
        if cls is None:
            # No class registered, just use a generic instance
            return HCI_Command(parameters, op_code=op_code)

        return cls.from_parameters(parameters)

    @classmethod
    def from_parameters(cls, parameters: bytes) -> HCI_Command:
        command = cls(**HCI_Object.dict_from_bytes(parameters, 0, cls.fields))
        command.parameters = parameters
        return command

    @classmethod
    def command_name(cls, op_code: int) -> str:
        if name := cls.command_names.get(op_code):
            return name
        if (subclass := cls.command_classes.get(op_code)) and subclass.name:
            return subclass.name
        return f'[OGF=0x{op_code >> 10:02x}, OCF=0x{op_code & 0x3FF:04x}]'

    @classmethod
    def create_return_parameters(cls, **kwargs):
        return HCI_Object(cls.return_parameters_fields, **kwargs)

    @classmethod
    def parse_return_parameters(cls, parameters):
        if not cls.return_parameters_fields:
            return None
        return_parameters = HCI_Object.from_bytes(
            parameters, 0, cls.return_parameters_fields
        )
        return_parameters.fields = cls.return_parameters_fields
        return return_parameters

    def __init__(
        self,
        parameters: Optional[bytes] = None,
        *,
        op_code: Optional[int] = None,
        **kwargs,
    ) -> None:
        # op_code should be set in cls.
        if op_code is not None:
            self.op_code = op_code
        super().__init__(HCI_Command.command_name(self.op_code))
        if self.fields and kwargs:
            HCI_Object.init_from_fields(self, self.fields, kwargs)
            if parameters is None:
                parameters = HCI_Object.dict_to_bytes(kwargs, self.fields)
        self.parameters = parameters or b''

    @property
    def parameters(self) -> bytes:
        if not self._parameters:
            self._parameters = HCI_Object.dict_to_bytes(self.__dict__, self.fields)
        return self._parameters

    @parameters.setter
    def parameters(self, parameters: bytes):
        self._parameters = parameters

    def __bytes__(self) -> bytes:
        parameters = b'' if self.parameters is None else self.parameters
        return (
            struct.pack('<BHB', HCI_COMMAND_PACKET, self.op_code, len(parameters))
            + parameters
        )

    def __str__(self):
        result = color(self.name, 'green')
        if self.fields:
            result += ':\n' + HCI_Object.format_fields(self.__dict__, self.fields, '  ')
        else:
            if self.parameters:
                result += f': {self.parameters.hex()}'
        return result


HCI_Command.register_commands(globals())


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Inquiry_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.1 Inquiry Command
    '''

    lap: int = field(
        metadata=metadata({'size': 3, 'mapper': HCI_Constant.inquiry_lap_name})
    )
    inquiry_length: int = field(metadata=metadata(1))
    num_responses: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Inquiry_Cancel_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.2 Inquiry Cancel Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Create_Connection_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.5 Create Connection Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    packet_type: int = field(metadata=metadata(2))
    page_scan_repetition_mode: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))
    clock_offset: int = field(metadata=metadata(2))
    allow_role_switch: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Disconnect_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.6 Disconnect Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Create_Connection_Cancel_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.7 Create Connection Cancel Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Accept_Connection_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.8 Accept Connection Request Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    role: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Reject_Connection_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.9 Reject Connection Request Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Link_Key_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.10 Link Key Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    link_key: bytes = field(metadata=metadata(16))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Link_Key_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.11 Link Key Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_PIN_Code_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.12 PIN Code Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    pin_code_length: int = field(metadata=metadata(1))
    pin_code: bytes = field(metadata=metadata(16))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_PIN_Code_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.13 PIN Code Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Change_Connection_Packet_Type_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.14 Change Connection Packet Type Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    packet_type: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Authentication_Requested_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.15 Authentication Requested Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Connection_Encryption_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.16 Set Connection Encryption Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    encryption_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Remote_Name_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.19 Remote Name Request Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    page_scan_repetition_mode: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))
    clock_offset: int = field(metadata=metadata(2))

    R0 = 0x00
    R1 = 0x01
    R2 = 0x02


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Remote_Supported_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.21 Read Remote Supported Features Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Remote_Extended_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.22 Read Remote Extended Features Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    page_number: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Remote_Version_Information_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.23 Read Remote Version Information Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Clock_Offset_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.23 Read Clock Offset Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Reject_Synchronous_Connection_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.28 Reject Synchronous Connection Request Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_IO_Capability_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.29 IO Capability Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    io_capability: int = field(metadata=IoCapability.type_metadata(1))
    oob_data_present: int = field(metadata=metadata(1))
    authentication_requirements: int = field(
        metadata=AuthenticationRequirements.type_metadata(1)
    )

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_User_Confirmation_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.30 User Confirmation Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_User_Confirmation_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.31 User Confirmation Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_User_Passkey_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.32 User Passkey Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    numeric_value: int = field(metadata=metadata(4))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_User_Passkey_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.33 User Passkey Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Remote_OOB_Data_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.34 Remote OOB Data Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    c: bytes = field(metadata=metadata(16))
    r: bytes = field(metadata=metadata(16))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Remote_OOB_Data_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.35 Remote OOB Data Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_IO_Capability_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.36 IO Capability Request Negative Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    reason: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Enhanced_Setup_Synchronous_Connection_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.45 Enhanced Setup Synchronous Connection Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    transmit_bandwidth: int = field(metadata=metadata(4))
    receive_bandwidth: int = field(metadata=metadata(4))
    transmit_coding_format: int = field(
        metadata=metadata(CodingFormat.parse_from_bytes)
    )
    receive_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    transmit_codec_frame_size: int = field(metadata=metadata(2))
    receive_codec_frame_size: int = field(metadata=metadata(2))
    input_bandwidth: int = field(metadata=metadata(4))
    output_bandwidth: int = field(metadata=metadata(4))
    input_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    output_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    input_coded_data_size: int = field(metadata=metadata(2))
    output_coded_data_size: int = field(metadata=metadata(2))
    input_pcm_data_format: int = field(metadata=metadata(1))
    output_pcm_data_format: int = field(metadata=metadata(1))
    input_pcm_sample_payload_msb_position: int = field(metadata=metadata(1))
    output_pcm_sample_payload_msb_position: int = field(metadata=metadata(1))
    input_data_path: int = field(metadata=metadata(1))
    output_data_path: int = field(metadata=metadata(1))
    input_transport_unit_size: int = field(metadata=metadata(1))
    output_transport_unit_size: int = field(metadata=metadata(1))
    max_latency: int = field(metadata=metadata(2))
    packet_type: int = field(metadata=metadata(2))
    retransmission_effort: int = field(metadata=metadata(1))

    class PcmDataFormat(SpecableEnum):
        NA = 0x00
        ONES_COMPLEMENT = 0x01
        TWOS_COMPLEMENT = 0x02
        SIGN_MAGNITUDE = 0x03
        UNSIGNED = 0x04

    class DataPath(SpecableEnum):
        HCI = 0x00
        PCM = 0x01

    class RetransmissionEffort(SpecableEnum):
        NO_RETRANSMISSION = 0x00
        OPTIMIZE_FOR_POWER = 0x01
        OPTIMIZE_FOR_QUALITY = 0x02
        DONT_CARE = 0xFF

    class PacketType(SpecableFlag):
        HV1 = 0x0001
        HV2 = 0x0002
        HV3 = 0x0004
        EV3 = 0x0008
        EV4 = 0x0010
        EV5 = 0x0020
        NO_2_EV3 = 0x0040
        NO_3_EV3 = 0x0080
        NO_2_EV5 = 0x0100
        NO_3_EV5 = 0x0200


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Enhanced_Accept_Synchronous_Connection_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.46 Enhanced Accept Synchronous Connection Request Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    transmit_bandwidth: int = field(metadata=metadata(4))
    receive_bandwidth: int = field(metadata=metadata(4))
    transmit_coding_format: int = field(
        metadata=metadata(CodingFormat.parse_from_bytes)
    )
    receive_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    transmit_codec_frame_size: int = field(metadata=metadata(2))
    receive_codec_frame_size: int = field(metadata=metadata(2))
    input_bandwidth: int = field(metadata=metadata(4))
    output_bandwidth: int = field(metadata=metadata(4))
    input_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    output_coding_format: int = field(metadata=metadata(CodingFormat.parse_from_bytes))
    input_coded_data_size: int = field(metadata=metadata(2))
    output_coded_data_size: int = field(metadata=metadata(2))
    input_pcm_data_format: int = field(metadata=metadata(1))
    output_pcm_data_format: int = field(metadata=metadata(1))
    input_pcm_sample_payload_msb_position: int = field(metadata=metadata(1))
    output_pcm_sample_payload_msb_position: int = field(metadata=metadata(1))
    input_data_path: int = field(metadata=metadata(1))
    output_data_path: int = field(metadata=metadata(1))
    input_transport_unit_size: int = field(metadata=metadata(1))
    output_transport_unit_size: int = field(metadata=metadata(1))
    max_latency: int = field(metadata=metadata(2))
    packet_type: int = field(metadata=metadata(2))
    retransmission_effort: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Truncated_Page_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.47 Truncated Page Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    page_scan_repetition_mode: int = field(metadata=metadata(1))
    clock_offset: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Truncated_Page_Cancel_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.48 Truncated Page Cancel Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Connectionless_Peripheral_Broadcast_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.49 Set Connectionless Peripheral Broadcast Command
    '''

    enable: int = field(metadata=metadata(1))
    lt_addr: Address = field(metadata=metadata(1))
    lpo_allowed: int = field(metadata=metadata(1))
    packet_type: int = field(metadata=metadata(2))
    interval_min: int = field(metadata=metadata(2))
    interval_max: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('lt_addr', 1),
        ('interval', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Connectionless_Peripheral_Broadcast_Receive_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.50 Set Connectionless Peripheral Broadcast Receive Command
    '''

    enable: int = field(metadata=metadata(1))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    lt_addr: Address = field(metadata=metadata(1))
    interval: int = field(metadata=metadata(2))
    clock_offset: int = field(metadata=metadata(4))
    next_connectionless_peripheral_broadcast_clock: int = field(metadata=metadata(4))
    supervision_timeout: int = field(metadata=metadata(2))
    remote_timing_accuracy: int = field(metadata=metadata(1))
    skip: int = field(metadata=metadata(1))
    packet_type: int = field(metadata=metadata(2))
    afh_channel_map: bytes = field(metadata=metadata(10))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
        ('lt_addr', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Start_Synchronization_Train_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.51 Start Synchronization Train Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Receive_Synchronization_Train_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.52 Receive Synchronization Train Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    sync_scan_timeout: int = field(metadata=metadata(2))
    sync_scan_window: int = field(metadata=metadata(2))
    sync_scan_interval: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Remote_OOB_Extended_Data_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.1.53 Remote OOB Extended Data Request Reply Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    c_192: bytes = field(metadata=metadata(16))
    r_192: bytes = field(metadata=metadata(16))
    c_256: bytes = field(metadata=metadata(16))
    r_256: bytes = field(metadata=metadata(16))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Sniff_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.2 Sniff Mode Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    sniff_max_interval: int = field(metadata=metadata(2))
    sniff_min_interval: int = field(metadata=metadata(2))
    sniff_attempt: int = field(metadata=metadata(2))
    sniff_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Exit_Sniff_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.3 Exit Sniff Mode Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Switch_Role_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.8 Switch Role Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    role: int = field(metadata=Role.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Link_Policy_Settings_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.10 Write Link Policy Settings Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    link_policy_settings: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Default_Link_Policy_Settings_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.12 Write Default Link Policy Settings Command
    '''

    default_link_policy_settings: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Sniff_Subrating_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.2.14 Sniff Subrating Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    maximum_latency: int = field(metadata=metadata(2))
    minimum_remote_timeout: int = field(metadata=metadata(2))
    minimum_local_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Event_Mask_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.1 Set Event Mask Command
    '''

    event_mask: bytes = field(metadata=metadata(8))

    @staticmethod
    def mask(event_codes: Iterable[int]) -> bytes:
        '''
        Compute the event mask value for a list of events.
        '''
        # NOTE: this implementation takes advantage of the fact that as of version 5.4
        # of the core specification, the bit number for each event code is equal to one
        # less than the event code.
        # If future versions of the specification deviate from that, a different
        # implementation would be needed.
        return sum((1 << event_code - 1) for event_code in event_codes).to_bytes(
            8, 'little'
        )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Reset_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.2 Reset Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Event_Filter_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.3 Set Event Filter Command
    '''

    filter_type: int = field(metadata=metadata(1))
    filter_condition: bytes = field(metadata=metadata("*"))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Stored_Link_Key_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.8 Read Stored Link Key Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    read_all_flag: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('max_num_keys', 2),
        ('num_keys_read', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Delete_Stored_Link_Key_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.10 Delete Stored Link Key Command
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    delete_all_flag: int = field(metadata=metadata(1))

    return_parameters_fields = [('status', STATUS_SPEC), ('num_keys_deleted', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Local_Name_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.11 Write Local Name Command
    '''

    local_name: bytes = field(
        metadata=metadata({'size': 248, 'mapper': map_null_terminated_utf8_string})
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Name_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.12 Read Local Name Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('local_name', {'size': 248, 'mapper': map_null_terminated_utf8_string}),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Connection_Accept_Timeout_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.14 Write Connection Accept Timeout Command
    '''

    connection_accept_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Page_Timeout_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.16 Write Page Timeout Command
    '''

    page_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Scan_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.18 Write Scan Enable Command
    '''

    scan_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Page_Scan_Activity_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.19 Read Page Scan Activity Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('page_scan_interval', 2),
        ('page_scan_window', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Page_Scan_Activity_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.20 Write Page Scan Activity Command
    '''

    page_scan_interval: int = field(metadata=metadata(2))
    page_scan_window: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Inquiry_Scan_Activity_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.22 Write Inquiry Scan Activity Command
    '''

    inquiry_scan_interval: int = field(metadata=metadata(2))
    inquiry_scan_window: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Authentication_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.23 Read Authentication Enable Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('authentication_enable', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Authentication_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.24 Write Authentication Enable Command
    '''

    authentication_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Class_Of_Device_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.25 Read Class of Device Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('class_of_device', COD_SPEC),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Class_Of_Device_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.26 Write Class of Device Command
    '''

    class_of_device: int = field(metadata=metadata(COD_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Voice_Setting_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.27 Read Voice Setting Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('voice_setting', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Voice_Setting_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.28 Write Voice Setting Command
    '''

    voice_setting: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Synchronous_Flow_Control_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.36 Read Synchronous Flow Control Enable Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('synchronous_flow_control_enable', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Synchronous_Flow_Control_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.37 Write Synchronous Flow Control Enable Command
    '''

    synchronous_flow_control_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Host_Buffer_Size_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.39 Host Buffer Size Command
    '''

    host_acl_data_packet_length: int = field(metadata=metadata(2))
    host_synchronous_data_packet_length: int = field(metadata=metadata(1))
    host_total_num_acl_data_packets: int = field(metadata=metadata(2))
    host_total_num_synchronous_data_packets: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Link_Supervision_Timeout_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.42 Write Link Supervision Timeout Command
    '''

    handle: int = field(metadata=metadata(2))
    link_supervision_timeout: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Number_Of_Supported_IAC_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.43 Read Number Of Supported IAC Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('num_support_iac', 1)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Current_IAC_LAP_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.44 Read Current IAC LAP Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('num_current_iac', 1),
        ('iac_lap', '*'),  # TODO: this should be parsed as an array
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Inquiry_Scan_Type_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.48 Write Inquiry Scan Type Command
    '''

    scan_type: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Inquiry_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.50 Write Inquiry Mode Command
    '''

    inquiry_mode: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Page_Scan_Type_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.51 Read Page Scan Type Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('page_scan_type', 1)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Page_Scan_Type_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.52 Write Page Scan Type Command
    '''

    page_scan_type: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Extended_Inquiry_Response_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.56 Write Extended Inquiry Response Command
    '''

    fec_required: int = field(metadata=metadata(1))
    extended_inquiry_response: int = field(
        metadata=metadata({'size': 240, 'serializer': lambda x: padded_bytes(x, 240)})
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Simple_Pairing_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.59 Write Simple Pairing Mode Command
    '''

    simple_pairing_mode: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_OOB_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.60 Read Local OOB Data Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('c', 16),
        ('r', 16),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Inquiry_Response_Transmit_Power_Level_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.61 Read Inquiry Response Transmit Power Level Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('tx_power', -1)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Default_Erroneous_Data_Reporting_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.64 Read Default Erroneous Data Reporting Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('erroneous_data_reporting', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Set_Event_Mask_Page_2_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.69 Set Event Mask Page 2 Command
    '''

    event_mask_page_2: bytes = field(metadata=metadata(8))

    @staticmethod
    def mask(event_codes: Iterable[int]) -> bytes:
        '''
        Compute the event mask value for a list of events.
        '''
        # NOTE: this implementation takes advantage of the fact that as of version 6.0
        # of the core specification, the bit number for each event code is equal to 64
        # less than the event code.
        # If future versions of the specification deviate from that, a different
        # implementation would be needed.
        return sum((1 << event_code - 64) for event_code in event_codes).to_bytes(
            8, 'little'
        )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_LE_Host_Support_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.78 Read LE Host Support Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('le_supported_host', 1),
        ('unused', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_LE_Host_Support_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.79 Write LE Host Support Command
    '''

    le_supported_host: int = field(metadata=metadata(1))
    simultaneous_le_host: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Secure_Connections_Host_Support_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.92 Write Secure Connections Host Support Command
    '''

    secure_connections_host_support: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Authenticated_Payload_Timeout_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.94 Write Authenticated Payload Timeout Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    authenticated_payload_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_OOB_Extended_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.3.95 Read Local OOB Extended Data Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('c_192', 16),
        ('r_192', 16),
        ('c_256', 16),
        ('r_256', 16),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Version_Information_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.1 Read Local Version Information Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('hci_version', 1),
        ('hci_subversion', 2),
        ('lmp_version', 1),
        ('company_identifier', 2),
        ('lmp_subversion', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Supported_Commands_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.2 Read Local Supported Commands Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('supported_commands', 64)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Supported_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.3 Read Local Supported Features Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('lmp_features', 8),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Extended_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.4 Read Local Extended Features Command
    '''

    page_number: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('page_number', 1),
        ('maximum_page_number', 1),
        ('extended_lmp_features', 8),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Buffer_Size_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.5 Read Buffer Size Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('hc_acl_data_packet_length', 2),
        ('hc_synchronous_data_packet_length', 1),
        ('hc_total_num_acl_data_packets', 2),
        ('hc_total_num_synchronous_data_packets', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_BD_ADDR_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.6 Read BD_ADDR Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('bd_addr', Address.parse_address),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Supported_Codecs_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.8 Read Local Supported Codecs Command
    '''

    return_parameters_fields = [
        ("status", STATUS_SPEC),
        [("standard_codec_ids", 1)],
        [("vendor_specific_codec_ids", 4)],
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Local_Supported_Codecs_V2_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.4.8 Read Local Supported Codecs Command
    '''

    return_parameters_fields = [
        ("status", STATUS_SPEC),
        [("standard_codec_ids", 1), ("standard_codec_transports", 1)],
        [("vendor_specific_codec_ids", 4), ("vendor_specific_codec_transports", 1)],
    ]

    class Transport(SpecableFlag):
        BR_EDR_ACL = 1 << 0
        BR_EDR_SCO = 1 << 1
        LE_CIS = 1 << 2
        LE_BIS = 1 << 3


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_RSSI_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.5.4 Read RSSI Command
    '''

    handle: int = field(metadata=metadata(2))

    return_parameters_fields = [('status', STATUS_SPEC), ('handle', 2), ('rssi', -1)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Encryption_Key_Size_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.5.7 Read Encryption Key Size Command
    '''

    connection_handle: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
        ('key_size', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Read_Loopback_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.6.1 Read Loopback Mode Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('loopback_mode', LoopbackMode.type_spec(1)),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_Write_Loopback_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.6.2 Write Loopback Mode Command
    '''

    loopback_mode: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Event_Mask_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.1 LE Set Event Mask Command
    '''

    le_event_mask: bytes = field(metadata=metadata(8))

    @staticmethod
    def mask(event_codes: Iterable[int]) -> bytes:
        '''
        Compute the event mask value for a list of events.
        '''
        # NOTE: this implementation takes advantage of the fact that as of version 5.4
        # of the core specification, the bit number for each event code is equal to one
        # less than the event code.
        # If future versions of the specification deviate from that, a different
        # implementation would be needed.
        return sum((1 << event_code - 1) for event_code in event_codes).to_bytes(
            8, 'little'
        )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Buffer_Size_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.2 LE Read Buffer Size Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('le_acl_data_packet_length', 2),
        ('total_num_le_acl_data_packets', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Buffer_Size_V2_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.2 LE Read Buffer Size V2 Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('le_acl_data_packet_length', 2),
        ('total_num_le_acl_data_packets', 1),
        ('iso_data_packet_length', 2),
        ('total_num_iso_data_packets', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Local_Supported_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.3 LE Read Local Supported Features Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('le_features', 8)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Random_Address_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.4 LE Set Random Address Command
    '''

    random_address: Address = field(
        metadata=metadata(
            lambda data, offset: Address.parse_address_with_type(
                data, offset, Address.RANDOM_DEVICE_ADDRESS
            )
        )
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Advertising_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.5 LE Set Advertising Parameters Command
    '''

    class AdvertisingType(SpecableEnum):
        ADV_IND = 0x00
        ADV_DIRECT_IND = 0x01
        ADV_SCAN_IND = 0x02
        ADV_NONCONN_IND = 0x03
        ADV_DIRECT_IND_LOW_DUTY = 0x04

    advertising_interval_min: int = field(metadata=metadata(2))
    advertising_interval_max: int = field(metadata=metadata(2))
    advertising_type: int = field(metadata=AdvertisingType.type_metadata(1))
    own_address_type: int = field(metadata=OwnAddressType.type_metadata(1))
    peer_address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertising_channel_map: int = field(metadata=metadata(1))
    advertising_filter_policy: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Advertising_Physical_Channel_Tx_Power_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.6 LE Read Advertising Physical Channel Tx Power Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('tx_power_level', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Advertising_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.7 LE Set Advertising Data Command
    '''

    advertising_data: bytes = field(
        metadata=metadata(
            {
                'parser': HCI_Object.parse_length_prefixed_bytes,
                'serializer': functools.partial(
                    HCI_Object.serialize_length_prefixed_bytes, padded_size=32
                ),
            }
        )
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Scan_Response_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.8 LE Set Scan Response Data Command
    '''

    scan_response_data: bytes = field(
        metadata=metadata(
            {
                'parser': HCI_Object.parse_length_prefixed_bytes,
                'serializer': functools.partial(
                    HCI_Object.serialize_length_prefixed_bytes, padded_size=32
                ),
            }
        )
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Advertising_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.9 LE Set Advertising Enable Command
    '''

    advertising_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Scan_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.10 LE Set Scan Parameters Command
    '''

    le_scan_type: int = field(metadata=metadata(1))
    le_scan_interval: int = field(metadata=metadata(2))
    le_scan_window: int = field(metadata=metadata(2))
    own_address_type: int = field(metadata=OwnAddressType.type_metadata(1))
    scanning_filter_policy: int = field(metadata=metadata(1))

    PASSIVE_SCANNING = 0
    ACTIVE_SCANNING = 1

    BASIC_UNFILTERED_POLICY = 0x00
    BASIC_FILTERED_POLICY = 0x01
    EXTENDED_UNFILTERED_POLICY = 0x02
    EXTENDED_FILTERED_POLICY = 0x03


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Scan_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.11 LE Set Scan Enable Command
    '''

    le_scan_enable: int = field(metadata=metadata(1))
    filter_duplicates: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Create_Connection_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.12 LE Create Connection Command
    '''

    le_scan_interval: int = field(metadata=metadata(2))
    le_scan_window: int = field(metadata=metadata(2))
    initiator_filter_policy: int = field(metadata=metadata(1))
    peer_address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    own_address_type: int = field(metadata=OwnAddressType.type_metadata(1))
    connection_interval_min: int = field(metadata=metadata(2))
    connection_interval_max: int = field(metadata=metadata(2))
    max_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))
    min_ce_length: int = field(metadata=metadata(2))
    max_ce_length: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Create_Connection_Cancel_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.13 LE Create Connection Cancel Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Filter_Accept_List_Size_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.14 LE Read Filter Accept List Size Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('filter_accept_list_size', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Clear_Filter_Accept_List_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.15 LE Clear Filter Accept List Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Add_Device_To_Filter_Accept_List_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.16 LE Add Device To Filter Accept List Command
    '''

    address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    address: Address = field(metadata=metadata(Address.parse_address_preceded_by_type))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remove_Device_From_Filter_Accept_List_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.17 LE Remove Device From Filter Accept List Command
    '''

    address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    address: Address = field(metadata=metadata(Address.parse_address_preceded_by_type))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Connection_Update_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.18 LE Connection Update Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    connection_interval_min: int = field(metadata=metadata(2))
    connection_interval_max: int = field(metadata=metadata(2))
    max_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))
    min_ce_length: int = field(metadata=metadata(2))
    max_ce_length: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Remote_Features_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.21 LE Read Remote Features Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Rand_Command(HCI_Command):
    """
    See Bluetooth spec @ 7.8.23 LE Rand Command
    """

    return_parameters_fields = [("status", STATUS_SPEC), ("random_number", 8)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Enable_Encryption_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.24 LE Enable Encryption Command
    (renamed from "LE Start Encryption Command" in version prior to 5.2 of the
    specification)
    '''

    connection_handle: int = field(metadata=metadata(2))
    random_number: bytes = field(metadata=metadata(8))
    encrypted_diversifier: int = field(metadata=metadata(2))
    long_term_key: bytes = field(metadata=metadata(16))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Long_Term_Key_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.25 LE Long Term Key Request Reply Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    long_term_key: bytes = field(metadata=metadata(16))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Long_Term_Key_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.26 LE Long Term Key Request Negative Reply Command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Supported_States_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.27 LE Read Supported States Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('le_states', 8),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remote_Connection_Parameter_Request_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.31 LE Remote Connection Parameter Request Reply Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    interval_min: int = field(metadata=metadata(2))
    interval_max: int = field(metadata=metadata(2))
    max_latency: int = field(metadata=metadata(2))
    timeout: int = field(metadata=metadata(2))
    min_ce_length: int = field(metadata=metadata(2))
    max_ce_length: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remote_Connection_Parameter_Request_Negative_Reply_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.32 LE Remote Connection Parameter Request Negative Reply
    Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Data_Length_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.33 LE Set Data Length Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    tx_octets: int = field(metadata=metadata(2))
    tx_time: int = field(metadata=metadata(2))

    return_parameters_fields = [('status', STATUS_SPEC), ('connection_handle', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Suggested_Default_Data_Length_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.34 LE Read Suggested Default Data Length Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('suggested_max_tx_octets', 2),
        ('suggested_max_tx_time', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Write_Suggested_Default_Data_Length_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.35 LE Write Suggested Default Data Length Command
    '''

    suggested_max_tx_octets: int = field(metadata=metadata(2))
    suggested_max_tx_time: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Add_Device_To_Resolving_List_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.38 LE Add Device To Resolving List Command
    '''

    peer_identity_address_type: int = field(
        metadata=metadata(Address.ADDRESS_TYPE_SPEC)
    )
    peer_identity_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    peer_irk: bytes = field(metadata=metadata(16))
    local_irk: bytes = field(metadata=metadata(16))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Clear_Resolving_List_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.40 LE Clear Resolving List Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Address_Resolution_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.44 LE Set Address Resolution Enable Command
    '''

    address_resolution_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Resolvable_Private_Address_Timeout_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.45 LE Set Resolvable Private Address Timeout Command
    '''

    rpa_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Maximum_Data_Length_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.46 LE Read Maximum Data Length Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('supported_max_tx_octets', 2),
        ('supported_max_tx_time', 2),
        ('supported_max_rx_octets', 2),
        ('supported_max_rx_time', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_PHY_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.47 LE Read PHY Command
    '''

    connection_handle: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
        ('tx_phy', Phy.type_spec(1)),
        ('rx_phy', Phy.type_spec(1)),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Default_PHY_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.48 LE Set Default PHY Command
    '''

    class AnyPhy(SpecableFlag):
        ANY_TX = 0
        ANY_RX = 1

    all_phys: int = field(metadata=AnyPhy.type_metadata(1))
    tx_phys: int = field(metadata=PhyBit.type_metadata(1))
    rx_phys: int = field(metadata=PhyBit.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_PHY_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.49 LE Set PHY Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    all_phys: int = field(
        metadata=HCI_LE_Set_Default_PHY_Command.AnyPhy.type_metadata(1)
    )
    tx_phys: int = field(metadata=PhyBit.type_metadata(1))
    rx_phys: int = field(metadata=PhyBit.type_metadata(1))
    phy_options: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Advertising_Set_Random_Address_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.52 LE Set Advertising Set Random Address Command
    '''

    advertising_handle: int = field(metadata=metadata(1))
    random_address: Address = field(
        metadata=metadata(
            lambda data, offset: Address.parse_address_with_type(
                data, offset, Address.RANDOM_DEVICE_ADDRESS
            )
        )
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Extended_Advertising_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.53 LE Set Extended Advertising Parameters Command
    '''

    return_parameters_fields = [('status', STATUS_SPEC), ('selected_tx_power', 1)]

    TX_POWER_NO_PREFERENCE = 0x7F
    SHOULD_NOT_FRAGMENT = 0x01

    class AdvertisingProperties(SpecableFlag):
        CONNECTABLE_ADVERTISING = 1 << 0
        SCANNABLE_ADVERTISING = 1 << 1
        DIRECTED_ADVERTISING = 1 << 2
        HIGH_DUTY_CYCLE_DIRECTED_CONNECTABLE_ADVERTISING = 1 << 3
        USE_LEGACY_ADVERTISING_PDUS = 1 << 4
        ANONYMOUS_ADVERTISING = 1 << 5
        INCLUDE_TX_POWER = 1 << 6

        def __str__(self) -> str:
            return '|'.join(
                flag.name
                for flag in HCI_LE_Set_Extended_Advertising_Parameters_Command.AdvertisingProperties
                if self.value & flag.value and flag.name is not None
            )

    class ChannelMap(SpecableFlag):
        CHANNEL_37 = 1 << 0
        CHANNEL_38 = 1 << 1
        CHANNEL_39 = 1 << 2

        def __str__(self) -> str:
            return '|'.join(
                flag.name
                for flag in HCI_LE_Set_Extended_Advertising_Parameters_Command.ChannelMap
                if self.value & flag.value and flag.name is not None
            )

    advertising_handle: int = field(metadata=metadata(1))
    advertising_event_properties: int = field(
        metadata=AdvertisingProperties.type_metadata(2)
    )
    primary_advertising_interval_min: int = field(metadata=metadata(3))
    primary_advertising_interval_max: int = field(metadata=metadata(3))
    primary_advertising_channel_map: int = field(metadata=ChannelMap.type_metadata(1))
    own_address_type: int = field(metadata=OwnAddressType.type_metadata(1))
    peer_address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertising_filter_policy: int = field(metadata=metadata(1))
    advertising_tx_power: int = field(metadata=metadata(1))
    primary_advertising_phy: int = field(metadata=Phy.type_metadata(1))
    secondary_advertising_max_skip: int = field(metadata=metadata(1))
    secondary_advertising_phy: int = field(metadata=Phy.type_metadata(1))
    advertising_sid: int = field(metadata=metadata(1))
    scan_request_notification_enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Extended_Advertising_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.54 LE Set Extended Advertising Data Command
    '''

    class Operation(SpecableEnum):
        INTERMEDIATE_FRAGMENT = 0x00
        FIRST_FRAGMENT = 0x01
        LAST_FRAGMENT = 0x02
        COMPLETE_DATA = 0x03
        UNCHANGED_DATA = 0x04

    advertising_handle: int = field(metadata=metadata(1))
    operation: int = field(metadata=Operation.type_metadata(1))
    fragment_preference: int = field(metadata=metadata(1))
    advertising_data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Extended_Scan_Response_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.55 LE Set Extended Scan Response Data Command
    '''

    advertising_handle: int = field(metadata=metadata(1))
    operation: int = field(
        metadata=HCI_LE_Set_Extended_Advertising_Data_Command.Operation.type_metadata(1)
    )
    fragment_preference: int = field(metadata=metadata(1))
    scan_response_data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Extended_Advertising_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.56 LE Set Extended Advertising Enable Command
    '''

    enable: int = field(metadata=metadata(1))
    advertising_handles: Sequence[int] = field(metadata=metadata(1, list_begin=True))
    durations: Sequence[int] = field(metadata=metadata(2))
    max_extended_advertising_events: Sequence[int] = field(
        metadata=metadata(1, list_end=True)
    )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Maximum_Advertising_Data_Length_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.57 LE Read Maximum Advertising Data Length Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('max_advertising_data_length', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_Number_Of_Supported_Advertising_Sets_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.58 LE Read Number of Supported Advertising Sets Command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('num_supported_advertising_sets', 1),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remove_Advertising_Set_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.59 LE Remove Advertising Set Command
    '''

    advertising_handle: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Clear_Advertising_Sets_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.60 LE Clear Advertising Sets Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Periodic_Advertising_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.61 LE Set Periodic Advertising Parameters command
    '''

    advertising_handle: int = field(metadata=metadata(1))
    periodic_advertising_interval_min: int = field(metadata=metadata(2))
    periodic_advertising_interval_max: int = field(metadata=metadata(2))
    periodic_advertising_properties: int = field(metadata=metadata(2))

    class Properties(SpecableFlag):
        INCLUDE_TX_POWER = 1 << 6


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Periodic_Advertising_Data_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.62 LE Set Periodic Advertising Data command
    '''

    advertising_handle: int = field(metadata=metadata(1))
    operation: int = field(
        metadata=HCI_LE_Set_Extended_Advertising_Data_Command.Operation.type_metadata(1)
    )
    advertising_data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Periodic_Advertising_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.63 LE Set Periodic Advertising Enable Command
    '''

    enable: int = field(metadata=metadata(1))
    advertising_handle: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
class HCI_LE_Set_Extended_Scan_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.64 LE Set Extended Scan Parameters Command
    '''

    PASSIVE_SCANNING = 0
    ACTIVE_SCANNING = 1

    BASIC_UNFILTERED_POLICY = 0x00
    BASIC_FILTERED_POLICY = 0x01
    EXTENDED_UNFILTERED_POLICY = 0x02
    EXTENDED_FILTERED_POLICY = 0x03

    op_code = HCI_LE_SET_EXTENDED_SCAN_PARAMETERS_COMMAND

    @classmethod
    def from_parameters(cls, parameters: bytes) -> Self:
        own_address_type = parameters[0]
        scanning_filter_policy = parameters[1]
        scanning_phys = parameters[2]

        phy_bits_set = bin(scanning_phys).count('1')
        scan_types = []
        scan_intervals = []
        scan_windows = []
        for i in range(phy_bits_set):
            scan_types.append(parameters[3 + (5 * i)])
            scan_intervals.append(
                struct.unpack_from('<H', parameters, 3 + (5 * i) + 1)[0]
            )
            scan_windows.append(
                struct.unpack_from('<H', parameters, 3 + (5 * i) + 3)[0]
            )

        return cls(
            own_address_type=own_address_type,
            scanning_filter_policy=scanning_filter_policy,
            scanning_phys=scanning_phys,
            scan_types=scan_types,
            scan_intervals=scan_intervals,
            scan_windows=scan_windows,
        )

    def __init__(
        self,
        own_address_type: int,
        scanning_filter_policy: int,
        scanning_phys: int,
        scan_types: Sequence[int],
        scan_intervals: Sequence[int],
        scan_windows: Sequence[int],
    ) -> None:
        super().__init__()
        self.own_address_type = own_address_type
        self.scanning_filter_policy = scanning_filter_policy
        self.scanning_phys = scanning_phys
        self.scan_types = list(scan_types)
        self.scan_intervals = list(scan_intervals)
        self.scan_windows = list(scan_windows)

        self.parameters = bytes(
            [own_address_type, scanning_filter_policy, scanning_phys]
        )
        phy_bits_set = bin(scanning_phys).count('1')
        for i in range(phy_bits_set):
            self.parameters += struct.pack(
                '<BHH', scan_types[i], scan_intervals[i], scan_windows[i]
            )

    def __str__(self):
        scanning_phys_strs = bit_flags_to_strings(
            self.scanning_phys, HCI_LE_PHY_BIT_NAMES
        )
        fields = [
            (
                'own_address_type:      ',
                Address.address_type_name(self.own_address_type),
            ),
            ('scanning_filter_policy:', self.scanning_filter_policy),
            ('scanning_phys:         ', ','.join(scanning_phys_strs)),
        ]
        for i, scanning_phy_str in enumerate(scanning_phys_strs):
            fields.append(
                (
                    f'{scanning_phy_str}.scan_type:    ',
                    (
                        'PASSIVE'
                        if self.scan_types[i] == self.PASSIVE_SCANNING
                        else 'ACTIVE'
                    ),
                )
            )
            fields.append(
                (f'{scanning_phy_str}.scan_interval:', self.scan_intervals[i])
            )
            fields.append((f'{scanning_phy_str}.scan_window:  ', self.scan_windows[i]))

        return (
            color(self.name, 'green')
            + ':\n'
            + '\n'.join(
                [
                    color('  ' + field[0], 'cyan') + ' ' + str(field[1])
                    for field in fields
                ]
            )
        )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Extended_Scan_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.65 LE Set Extended Scan Enable Command
    '''

    enable: int = field(metadata=metadata(1))
    filter_duplicates: int = field(metadata=metadata(1))
    duration: int = field(metadata=metadata(2))
    period: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
class HCI_LE_Extended_Create_Connection_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.66 LE Extended Create Connection Command
    '''

    op_code = HCI_LE_EXTENDED_CREATE_CONNECTION_COMMAND

    @classmethod
    def from_parameters(cls, parameters: bytes) -> Self:
        initiator_filter_policy = parameters[0]
        own_address_type = parameters[1]
        peer_address_type = parameters[2]
        peer_address = Address.parse_address_preceded_by_type(parameters, 3)[1]
        initiating_phys = parameters[9]

        phy_bits_set = bin(initiating_phys).count('1')

        def read_parameter_list(offset):
            return [
                struct.unpack_from('<H', parameters, offset + 16 * i)[0]
                for i in range(phy_bits_set)
            ]

        return cls(
            initiator_filter_policy=initiator_filter_policy,
            own_address_type=own_address_type,
            peer_address_type=peer_address_type,
            peer_address=peer_address,
            initiating_phys=initiating_phys,
            scan_intervals=read_parameter_list(10),
            scan_windows=read_parameter_list(12),
            connection_interval_mins=read_parameter_list(14),
            connection_interval_maxs=read_parameter_list(16),
            max_latencies=read_parameter_list(18),
            supervision_timeouts=read_parameter_list(20),
            min_ce_lengths=read_parameter_list(22),
            max_ce_lengths=read_parameter_list(24),
        )

    def __init__(
        self,
        initiator_filter_policy: int,
        own_address_type: int,
        peer_address_type: int,
        peer_address: Address,
        initiating_phys: int,
        scan_intervals: Sequence[int],
        scan_windows: Sequence[int],
        connection_interval_mins: Sequence[int],
        connection_interval_maxs: Sequence[int],
        max_latencies: Sequence[int],
        supervision_timeouts: Sequence[int],
        min_ce_lengths: Sequence[int],
        max_ce_lengths: Sequence[int],
    ):
        super().__init__()
        self.initiator_filter_policy = initiator_filter_policy
        self.own_address_type = own_address_type
        self.peer_address_type = peer_address_type
        self.peer_address = peer_address
        self.initiating_phys = initiating_phys
        self.scan_intervals = list(scan_intervals)
        self.scan_windows = list(scan_windows)
        self.connection_interval_mins = list(connection_interval_mins)
        self.connection_interval_maxs = list(connection_interval_maxs)
        self.max_latencies = list(max_latencies)
        self.supervision_timeouts = list(supervision_timeouts)
        self.min_ce_lengths = list(min_ce_lengths)
        self.max_ce_lengths = list(max_ce_lengths)

        self.parameters = (
            bytes([initiator_filter_policy, own_address_type, peer_address_type])
            + bytes(peer_address)
            + bytes([initiating_phys])
        )

        phy_bits_set = bin(initiating_phys).count('1')
        for i in range(phy_bits_set):
            self.parameters += struct.pack(
                '<HHHHHHHH',
                scan_intervals[i],
                scan_windows[i],
                connection_interval_mins[i],
                connection_interval_maxs[i],
                max_latencies[i],
                supervision_timeouts[i],
                min_ce_lengths[i],
                max_ce_lengths[i],
            )

    def __str__(self):
        initiating_phys_strs = bit_flags_to_strings(
            self.initiating_phys, HCI_LE_PHY_BIT_NAMES
        )
        fields = [
            ('initiator_filter_policy:', self.initiator_filter_policy),
            (
                'own_address_type:       ',
                OwnAddressType(self.own_address_type).name,
            ),
            (
                'peer_address_type:      ',
                Address.address_type_name(self.peer_address_type),
            ),
            ('peer_address:           ', str(self.peer_address)),
            ('initiating_phys:        ', ','.join(initiating_phys_strs)),
        ]
        for i, initiating_phys_str in enumerate(initiating_phys_strs):
            fields.append(
                (
                    f'{initiating_phys_str}.scan_interval:          ',
                    self.scan_intervals[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.scan_window:            ',
                    self.scan_windows[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.connection_interval_min:',
                    self.connection_interval_mins[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.connection_interval_max:',
                    self.connection_interval_maxs[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.max_latency:            ',
                    self.max_latencies[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.supervision_timeout:    ',
                    self.supervision_timeouts[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.min_ce_length:          ',
                    self.min_ce_lengths[i],
                )
            )
            fields.append(
                (
                    f'{initiating_phys_str}.max_ce_length:          ',
                    self.max_ce_lengths[i],
                )
            )

        return (
            color(self.name, 'green')
            + ':\n'
            + '\n'.join(
                [
                    color('  ' + field[0], 'cyan') + ' ' + str(field[1])
                    for field in fields
                ]
            )
        )


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Create_Sync_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.67 LE Periodic Advertising Create Sync command
    '''

    class Options(SpecableFlag):
        USE_PERIODIC_ADVERTISER_LIST = 1 << 0
        REPORTING_INITIALLY_DISABLED = 1 << 1
        DUPLICATE_FILTERING_INITIALLY_ENABLED = 1 << 2

    class CteType(SpecableFlag):
        DO_NOT_SYNC_TO_PACKETS_WITH_AN_AOA_CONSTANT_TONE_EXTENSION = 1 << 0
        DO_NOT_SYNC_TO_PACKETS_WITH_AN_AOD_CONSTANT_TONE_EXTENSION_1US = 1 << 1
        DO_NOT_SYNC_TO_PACKETS_WITH_AN_AOD_CONSTANT_TONE_EXTENSION_2US = 1 << 2
        DO_NOT_SYNC_TO_PACKETS_WITH_A_TYPE_3_CONSTANT_TONE_EXTENSION = 1 << 3
        DO_NOT_SYNC_TO_PACKETS_WITHOUT_A_CONSTANT_TONE_EXTENSION = 1 << 4

    options: int = field(metadata=Options.type_metadata(1))
    advertising_sid: int = field(metadata=metadata(1))
    advertiser_address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
    advertiser_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    skip: int = field(metadata=metadata(2))
    sync_timeout: int = field(metadata=metadata(2))
    sync_cte_type: int = field(metadata=CteType.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Create_Sync_Cancel_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.68 LE Periodic Advertising Create Sync Cancel Command
    '''


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Terminate_Sync_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.69 LE Periodic Advertising Terminate Sync Command
    '''

    sync_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Privacy_Mode_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.77 LE Set Privacy Mode Command
    '''

    class PrivacyMode(SpecableEnum):
        NETWORK_PRIVACY_MODE = 0x00
        DEVICE_PRIVACY_MODE = 0x01

    peer_identity_address_type: int = field(
        metadata=metadata(Address.ADDRESS_TYPE_SPEC)
    )
    peer_identity_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    privacy_mode: int = field(metadata=PrivacyMode.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Periodic_Advertising_Receive_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.88 LE Set Periodic Advertising Receive Enable Command
    '''

    sync_handle: int = field(metadata=metadata(2))
    enable: int = field(metadata=metadata(1))

    class Enable(SpecableFlag):
        REPORTING_ENABLED = 1 << 0
        DUPLICATE_FILTERING_ENABLED = 1 << 1


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Transfer_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.89 LE Periodic Advertising Sync Transfer Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    service_data: int = field(metadata=metadata(2))
    sync_handle: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Set_Info_Transfer_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.90 LE Periodic Advertising Set Info Transfer Command
    '''

    connection_handle: int = field(metadata=metadata(2))
    service_data: int = field(metadata=metadata(2))
    advertising_handle: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Periodic_Advertising_Sync_Transfer_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.91 LE Set Periodic Advertising Sync Transfer Parameters command
    '''

    connection_handle: int = field(metadata=metadata(2))
    mode: int = field(metadata=metadata(1))
    skip: int = field(metadata=metadata(2))
    sync_timeout: int = field(metadata=metadata(2))
    cte_type: int = field(metadata=CteType.type_metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Default_Periodic_Advertising_Sync_Transfer_Parameters_Command(
    HCI_Command
):
    '''
    See Bluetooth spec @ 7.8.92 LE Set Default Periodic Advertising Sync Transfer Parameters command
    '''

    mode: int = field(metadata=metadata(1))
    skip: int = field(metadata=metadata(2))
    sync_timeout: int = field(metadata=metadata(2))
    cte_type: int = field(metadata=CteType.type_metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Read_ISO_TX_Sync_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.96 LE Read ISO TX Sync command
    '''

    connection_handle: int = field(metadata=metadata(2))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
        ('packet_sequence_number', 2),
        ('tx_time_stamp', 4),
        ('time_offset', 3),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_CIG_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.97 LE Set CIG Parameters Command
    '''

    cig_id: int = field(metadata=metadata(1))
    sdu_interval_c_to_p: int = field(metadata=metadata(3))
    sdu_interval_p_to_c: int = field(metadata=metadata(3))
    worst_case_sca: int = field(metadata=metadata(1))
    packing: int = field(metadata=metadata(1))
    framing: int = field(metadata=metadata(1))
    max_transport_latency_c_to_p: int = field(metadata=metadata(2))
    max_transport_latency_p_to_c: int = field(metadata=metadata(2))
    cis_id: Sequence[int] = field(metadata=metadata(1, list_begin=True))
    max_sdu_c_to_p: Sequence[int] = field(metadata=metadata(2))
    max_sdu_p_to_c: Sequence[int] = field(metadata=metadata(2))
    phy_c_to_p: Sequence[int] = field(metadata=metadata(1))
    phy_p_to_c: Sequence[int] = field(metadata=metadata(1))
    rtn_c_to_p: Sequence[int] = field(metadata=metadata(1))
    rtn_p_to_c: Sequence[int] = field(metadata=metadata(1, list_end=True))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('cig_id', 1),
        [('connection_handle', 2)],
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Create_CIS_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.99 LE Create CIS command
    '''

    cis_connection_handle: Sequence[int] = field(metadata=metadata(2, list_begin=True))
    acl_connection_handle: Sequence[int] = field(metadata=metadata(2, list_end=True))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remove_CIG_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.100 LE Remove CIG command
    '''

    cig_id: int = field(metadata=metadata(1))

    return_parameters_fields = [('status', STATUS_SPEC), ('cig_id', 1)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Accept_CIS_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.101 LE Accept CIS Request command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Reject_CIS_Request_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.102 LE Reject CIS Request command
    '''

    connection_handle: int = field(metadata=metadata(2))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Create_BIG_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.103 LE Create BIG command
    '''

    big_handle: int = field(metadata=metadata(1))
    advertising_handle: int = field(metadata=metadata(1))
    num_bis: int = field(metadata=metadata(1))
    sdu_interval: int = field(metadata=metadata(3))
    max_sdu: int = field(metadata=metadata(2))
    max_transport_latency: int = field(metadata=metadata(2))
    rtn: int = field(metadata=metadata(1))
    phy: int = field(metadata=metadata(1))
    packing: int = field(metadata=metadata(1))
    framing: int = field(metadata=metadata(1))
    encryption: int = field(metadata=metadata(1))
    broadcast_code: bytes = field(metadata=metadata(16))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Terminate_BIG_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.105 LE Terminate BIG command
    '''

    big_handle: int = field(metadata=metadata(1))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_BIG_Create_Sync_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.106 LE BIG Create Sync command
    '''

    big_handle: int = field(metadata=metadata(1))
    sync_handle: int = field(metadata=metadata(2))
    encryption: int = field(metadata=metadata(1))
    broadcast_code: bytes = field(metadata=metadata(16))
    mse: int = field(metadata=metadata(1))
    big_sync_timeout: int = field(metadata=metadata(2))
    bis: Sequence[int] = field(metadata=metadata(1, list_begin=True, list_end=True))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_BIG_Terminate_Sync_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.107. LE BIG Terminate Sync command
    '''

    big_handle: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('big_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Setup_ISO_Data_Path_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.109 LE Setup ISO Data Path command
    '''

    class Direction(SpecableEnum):
        HOST_TO_CONTROLLER = 0x00
        CONTROLLER_TO_HOST = 0x01

    connection_handle: int = field(metadata=metadata(2))
    data_path_direction: int = field(metadata=Direction.type_metadata(1))
    data_path_id: int = field(metadata=metadata(1))
    codec_id: CodingFormat = field(metadata=metadata(CodingFormat.parse_from_bytes))
    controller_delay: int = field(metadata=metadata(3))
    codec_configuration: bytes = field(metadata=metadata("v"))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Remove_ISO_Data_Path_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.110 LE Remove ISO Data Path command
    '''

    connection_handle: int = field(metadata=metadata(2))
    data_path_direction: int = field(metadata=metadata(1))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_Set_Host_Feature_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.115 LE Set Host Feature Command
    '''

    bit_number: int = field(metadata=metadata(1))
    bit_value: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Read_Local_Supported_Capabilities_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.130 LE CS Read Local Supported Capabilities command
    '''

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('num_config_supported', 1),
        ('max_consecutive_procedures_supported', 2),
        ('num_antennas_supported', 1),
        ('max_antenna_paths_supported', 1),
        ('roles_supported', 1),
        ('modes_supported', 1),
        ('rtt_capability', 1),
        ('rtt_aa_only_n', 1),
        ('rtt_sounding_n', 1),
        ('rtt_random_payload_n', 1),
        ('nadm_sounding_capability', 2),
        ('nadm_random_capability', 2),
        ('cs_sync_phys_supported', CS_SYNC_PHY_SUPPORTED_SPEC),
        ('subfeatures_supported', 2),
        ('t_ip1_times_supported', 2),
        ('t_ip2_times_supported', 2),
        ('t_fcs_times_supported', 2),
        ('t_pm_times_supported', 2),
        ('t_sw_time_supported', 1),
        ('tx_snr_capability', CS_SNR_SPEC),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Read_Remote_Supported_Capabilities_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.131 LE CS Read Remote Supported Capabilities command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Write_Cached_Remote_Supported_Capabilities_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.132 LE CS Write Cached Remote Supported Capabilities command
    '''

    connection_handle: int = field(metadata=metadata(2))
    num_config_supported: int = field(metadata=metadata(1))
    max_consecutive_procedures_supported: int = field(metadata=metadata(2))
    num_antennas_supported: int = field(metadata=metadata(1))
    max_antenna_paths_supported: int = field(metadata=metadata(1))
    roles_supported: int = field(metadata=metadata(1))
    modes_supported: int = field(metadata=metadata(1))
    rtt_capability: int = field(metadata=metadata(1))
    rtt_aa_only_n: int = field(metadata=metadata(1))
    rtt_sounding_n: int = field(metadata=metadata(1))
    rtt_random_payload_n: int = field(metadata=metadata(1))
    nadm_sounding_capability: int = field(metadata=metadata(2))
    nadm_random_capability: int = field(metadata=metadata(2))
    cs_sync_phys_supported: int = field(metadata=metadata(CS_SYNC_PHY_SUPPORTED_SPEC))
    subfeatures_supported: int = field(metadata=metadata(2))
    t_ip1_times_supported: int = field(metadata=metadata(2))
    t_ip2_times_supported: int = field(metadata=metadata(2))
    t_fcs_times_supported: int = field(metadata=metadata(2))
    t_pm_times_supported: int = field(metadata=metadata(2))
    t_sw_time_supported: int = field(metadata=metadata(1))
    tx_snr_capability: int = field(metadata=metadata(CS_SNR_SPEC))

    return_parameters_fields = [
        ('status', STATUS_SPEC),
        ('connection_handle', 2),
    ]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Security_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.133 LE CS Security Enable command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Set_Default_Settings_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.134 LE CS Security Enable command
    '''

    connection_handle: int = field(metadata=metadata(2))
    role_enable: int = field(metadata=metadata(CS_ROLE_MASK_SPEC))
    cs_sync_antenna_selection: int = field(metadata=metadata(1))
    max_tx_power: int = field(metadata=metadata(1))

    return_parameters_fields = [('status', STATUS_SPEC), ('connection_handle', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Read_Remote_FAE_Table_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.135 LE CS Read Remote FAE Table command
    '''

    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Write_Cached_Remote_FAE_Table_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.136  LE CS Write Cached Remote FAE Table command
    '''

    connection_handle: int = field(metadata=metadata(2))
    remote_fae_table: bytes = field(metadata=metadata(72))

    return_parameters_fields = [('status', STATUS_SPEC), ('connection_handle', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Create_Config_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.137 LE CS Create Config command
    '''

    class ChannelSelectionType(SpecableEnum):
        ALGO_3B = 0
        ALGO_3C = 1

    class Ch3cShape(SpecableEnum):
        HAT = 0x00
        X = 0x01

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    create_context: int = field(metadata=metadata(1))
    main_mode_type: int = field(metadata=metadata(1))
    sub_mode_type: int = field(metadata=metadata(1))
    min_main_mode_steps: int = field(metadata=metadata(1))
    max_main_mode_steps: int = field(metadata=metadata(1))
    main_mode_repetition: int = field(metadata=metadata(1))
    mode_0_steps: int = field(metadata=metadata(1))
    role: int = field(metadata=metadata(CS_ROLE_SPEC))
    rtt_type: int = field(metadata=metadata(RTT_TYPE_SPEC))
    cs_sync_phy: int = field(metadata=metadata(CS_SYNC_PHY_SPEC))
    channel_map: bytes = field(metadata=metadata(10))
    channel_map_repetition: int = field(metadata=metadata(1))
    channel_selection_type: int = field(metadata=ChannelSelectionType.type_metadata(1))
    ch3c_shape: int = field(metadata=Ch3cShape.type_metadata(1))
    ch3c_jump: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Remove_Config_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.138 LE CS Remove Config command
    '''

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Set_Channel_Classification_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.139 LE CS Set Channel Classification command
    '''

    channel_classification: bytes = field(metadata=metadata(10))

    return_parameters_fields = [('status', STATUS_SPEC)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Set_Procedure_Parameters_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.140 LE CS Set Procedure Parameters command
    '''

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    max_procedure_len: int = field(metadata=metadata(2))
    min_procedure_interval: int = field(metadata=metadata(2))
    max_procedure_interval: int = field(metadata=metadata(2))
    max_procedure_count: int = field(metadata=metadata(2))
    min_subevent_len: int = field(metadata=metadata(3))
    max_subevent_len: int = field(metadata=metadata(3))
    tone_antenna_config_selection: int = field(metadata=metadata(1))
    phy: int = field(metadata=metadata(1))
    tx_power_delta: int = field(metadata=metadata(1))
    preferred_peer_antenna: int = field(metadata=metadata(1))
    snr_control_initiator: int = field(metadata=metadata(CS_SNR_SPEC))
    snr_control_reflector: int = field(metadata=metadata(CS_SNR_SPEC))

    return_parameters_fields = [('status', STATUS_SPEC), ('connection_handle', 2)]


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Procedure_Enable_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.141 LE CS Procedure Enable command
    '''

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    enable: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Test_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.142 LE CS Test command
    '''

    main_mode_type: int = field(metadata=metadata(1))
    sub_mode_type: int = field(metadata=metadata(1))
    main_mode_repetition: int = field(metadata=metadata(1))
    mode_0_steps: int = field(metadata=metadata(1))
    role: int = field(metadata=metadata(CS_ROLE_SPEC))
    rtt_type: int = field(metadata=metadata(RTT_TYPE_SPEC))
    cs_sync_phy: int = field(metadata=metadata(CS_SYNC_PHY_SPEC))
    cs_sync_antenna_selection: int = field(metadata=metadata(1))
    subevent_len: int = field(metadata=metadata(3))
    subevent_interval: int = field(metadata=metadata(2))
    max_num_subevents: int = field(metadata=metadata(1))
    transmit_power_level: int = field(metadata=metadata(1))
    t_ip1_time: int = field(metadata=metadata(1))
    t_ip2_time: int = field(metadata=metadata(1))
    t_fcs_time: int = field(metadata=metadata(1))
    t_pm_time: int = field(metadata=metadata(1))
    t_sw_time: int = field(metadata=metadata(1))
    tone_antenna_config_selection: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))
    snr_control_initiator: int = field(metadata=metadata(CS_SNR_SPEC))
    snr_control_reflector: int = field(metadata=metadata(CS_SNR_SPEC))
    drbg_nonce: int = field(metadata=metadata(2))
    channel_map_repetition: int = field(metadata=metadata(1))
    override_config: int = field(metadata=metadata(2))
    override_parameters_data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_Command.command
@dataclasses.dataclass
class HCI_LE_CS_Test_End_Command(HCI_Command):
    '''
    See Bluetooth spec @ 7.8.143 LE CS Test End command
    '''


# -----------------------------------------------------------------------------
# HCI Events
# -----------------------------------------------------------------------------
class HCI_Event(HCI_Packet):
    '''
    See Bluetooth spec @ Vol 2, Part E - 5.4.4 HCI Event Packet
    '''

    hci_packet_type = HCI_EVENT_PACKET
    event_names: dict[int, str] = {}
    event_classes: dict[int, type[HCI_Event]] = {}
    vendor_factories: list[Callable[[bytes], Optional[HCI_Event]]] = []
    event_code: int
    fields: Fields = ()
    _parameters: bytes = b''

    _Event = TypeVar("_Event", bound="HCI_Event")

    @classmethod
    def event(cls, subclass: type[_Event]) -> type[_Event]:
        '''
        Decorator used to declare and register subclasses
        '''
        # Subclasses may set parameters as ClassVar, or inferred from class name.
        if not hasattr(subclass, 'name'):
            subclass.name = subclass.__name__.upper()
        if not hasattr(subclass, 'event_code'):
            event_code = key_with_value(subclass.event_names, subclass.name)
            if event_code is None:
                raise KeyError(f'event {subclass.name} not found in event_names')
            subclass.event_code = event_code

        if dataclasses.is_dataclass(subclass):
            subclass.fields = HCI_Object.fields_from_dataclass(subclass)

        # Register a factory for this class
        cls.event_classes[subclass.event_code] = subclass
        return subclass

    @staticmethod
    def event_map(symbols: dict[str, Any]) -> dict[int, str]:
        return {
            event_code: event_name
            for (event_name, event_code) in symbols.items()
            if event_name.startswith('HCI_')
            and not event_name.startswith('HCI_LE_')
            and event_name.endswith('_EVENT')
        }

    @classmethod
    def event_name(cls, event_code: int) -> str:
        if (subclass := cls.event_classes.get(event_code)) and subclass.name:
            return subclass.name
        return name_or_number(cls.event_names, event_code)

    @staticmethod
    def register_events(symbols: dict[str, Any]) -> None:
        HCI_Event.event_names.update(HCI_Event.event_map(symbols))

    @staticmethod
    def registered(event_class):
        event_class.name = event_class.__name__.upper()
        event_class.event_code = key_with_value(HCI_Event.event_names, event_class.name)
        if event_class.event_code is None:
            raise KeyError(f'event {event_class.name} not found in event_names')

        # Register a factory for this class
        HCI_Event.event_classes[event_class.event_code] = event_class

        return event_class

    @classmethod
    def add_vendor_factory(
        cls, factory: Callable[[bytes], Optional[HCI_Event]]
    ) -> None:
        cls.vendor_factories.append(factory)

    @classmethod
    def remove_vendor_factory(
        cls, factory: Callable[[bytes], Optional[HCI_Event]]
    ) -> None:
        if factory in cls.vendor_factories:
            cls.vendor_factories.remove(factory)

    @classmethod
    def from_bytes(cls, packet: bytes) -> HCI_Event:
        event_code = packet[1]
        length = packet[2]
        parameters = packet[3:]
        if len(parameters) != length:
            raise InvalidPacketError('invalid packet length')

        subclass: Optional[type[HCI_Event]]
        if event_code == HCI_LE_META_EVENT:
            # We do this dispatch here and not in the subclass in order to avoid call
            # loops
            subevent_code = parameters[0]
            subclass = HCI_LE_Meta_Event.subevent_classes.get(subevent_code)
            if subclass is None:
                # No class registered, just use a generic class instance
                return HCI_LE_Meta_Event(
                    subevent_code=subevent_code, parameters=parameters
                )
        elif event_code == HCI_VENDOR_EVENT:
            # Invoke all the registered factories to see if any of them can handle
            # the event
            for vendor_factory in cls.vendor_factories:
                if event := vendor_factory(parameters):
                    return event

            # No factory, or the factory could not create an instance,
            # return a generic vendor event
            return HCI_Vendor_Event(data=parameters)
        else:
            subclass = HCI_Event.event_classes.get(event_code)
            if subclass is None:
                # No class registered, just use a generic class instance
                return HCI_Event(event_code=event_code, parameters=parameters)

        # Invoke the factory to create a new instance
        return subclass.from_parameters(parameters)

    @classmethod
    def from_parameters(cls, parameters: bytes) -> Self:
        event = cls(**HCI_Object.dict_from_bytes(parameters, 0, cls.fields))
        event.parameters = parameters
        return event

    def __init__(
        self,
        parameters: Optional[bytes] = None,
        *,
        event_code: Optional[int] = None,
        **kwargs,
    ):
        if event_code is not None:
            self.event_code = event_code
        super().__init__(HCI_Event.event_name(self.event_code))
        if self.fields and kwargs:
            HCI_Object.init_from_fields(self, self.fields, kwargs)
            if parameters is None:
                parameters = HCI_Object.dict_to_bytes(kwargs, self.fields)
        self.parameters = parameters or b''

    @property
    def parameters(self) -> bytes:
        if not self._parameters:
            self._parameters = HCI_Object.dict_to_bytes(self.__dict__, self.fields)
        return self._parameters

    @parameters.setter
    def parameters(self, parameters: bytes):
        self._parameters = parameters

    def __bytes__(self) -> bytes:
        parameters = self.parameters
        return bytes([HCI_EVENT_PACKET, self.event_code, len(parameters)]) + parameters

    def __str__(self):
        result = color(self.name, 'magenta')
        if self.fields:
            result += ':\n' + HCI_Object.format_fields(self.__dict__, self.fields, '  ')
        else:
            if self.parameters:
                result += f': {self.parameters.hex()}'
        return result


HCI_Event.register_events(globals())


# -----------------------------------------------------------------------------
class HCI_Extended_Event(HCI_Event):
    '''
    HCI_Event subclass for events that have a subevent code.
    '''

    subevent_names: dict[int, str] = {}
    subevent_classes: dict[int, type[HCI_Extended_Event]] = {}
    subevent_code: int
    _parameters: bytes = b''

    _ExtendedEvent = TypeVar("_ExtendedEvent", bound="HCI_Extended_Event")

    # TODO: Remove type ignore after migrating HCI_Event.
    @classmethod
    def event(cls, subclass: type[_ExtendedEvent]) -> type[_ExtendedEvent]:  # type: ignore[override]
        '''
        Decorator used to declare and register subclasses
        '''
        # Subclasses may set parameters as ClassVar, or inferred from class name.
        if not hasattr(subclass, 'name'):
            subclass.name = subclass.__name__.upper()
        if not hasattr(subclass, 'subevent_code'):
            subevent_code = key_with_value(subclass.subevent_names, subclass.name)
            if subevent_code is None:
                raise KeyError(f'subevent {subclass.name} not found in subevent_names')
            subclass.subevent_code = subevent_code

        if dataclasses.is_dataclass(subclass):
            subclass.fields = HCI_Object.fields_from_dataclass(subclass)

        # Register a factory for this class
        cls.subevent_classes[subclass.subevent_code] = subclass

        return subclass

    @property
    def parameters(self) -> bytes:
        if not self._parameters:
            self._parameters = bytes([self.subevent_code]) + HCI_Object.dict_to_bytes(
                self.__dict__, self.fields
            )
        return self._parameters

    @parameters.setter
    def parameters(self, parameters: bytes):
        self._parameters = parameters

    @classmethod
    def subevent_name(cls, subevent_code: int) -> str:
        if subevent_name := cls.subevent_names.get(subevent_code):
            return subevent_name
        if (subclass := cls.subevent_classes.get(subevent_code)) and subclass.name:
            return subclass.name

        return f'{cls.__name__.upper()}[0x{subevent_code:02X}]'

    @staticmethod
    def subevent_map(symbols: dict[str, Any]) -> dict[int, str]:
        return {
            subevent_code: subevent_name
            for (subevent_name, subevent_code) in symbols.items()
            if subevent_name.startswith('HCI_') and subevent_name.endswith('_EVENT')
        }

    @classmethod
    def register_subevents(cls, symbols: dict[str, Any]) -> None:
        cls.subevent_names.update(cls.subevent_map(symbols))

    @classmethod
    def subclass_from_parameters(
        cls, parameters: bytes
    ) -> Optional[HCI_Extended_Event]:
        """
        Factory method that parses the subevent code, finds a registered subclass,
        and creates an instance if found.
        """
        subevent_code = parameters[0]
        if subclass := cls.subevent_classes.get(subevent_code):
            return subclass.from_parameters(parameters)

        return None

    @classmethod
    def from_parameters(cls, parameters: bytes) -> Self:
        """Factory method for subclasses (the subevent code has already been parsed)"""
        event = cls(**HCI_Object.dict_from_bytes(parameters, 1, cls.fields))
        event.parameters = parameters
        return event

    def __init__(
        self,
        parameters: Optional[bytes] = None,
        *,
        subevent_code: Optional[int] = None,
        **kwargs,
    ) -> None:
        if subevent_code is not None:
            self.subevent_code = subevent_code
        if parameters is None and self.fields and kwargs:
            parameters = bytes([self.subevent_code]) + HCI_Object.dict_to_bytes(
                kwargs, self.fields
            )
        super().__init__(parameters, **kwargs)

        # Override the name in order to adopt the subevent name instead
        self.name = self.subevent_name(self.subevent_code)


# -----------------------------------------------------------------------------
class HCI_LE_Meta_Event(HCI_Extended_Event):
    '''
    See Bluetooth spec @ 7.7.65 LE Meta Event
    '''

    event_code: int = HCI_LE_META_EVENT
    subevent_classes = {}

    @staticmethod
    def subevent_map(symbols: dict[str, Any]) -> dict[int, str]:
        return {
            subevent_code: subevent_name
            for (subevent_name, subevent_code) in symbols.items()
            if subevent_name.startswith('HCI_LE_') and subevent_name.endswith('_EVENT')
        }


HCI_LE_Meta_Event.register_subevents(globals())


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Connection_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.1 LE Connection Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    role: int = field(metadata=Role.type_metadata(1))
    peer_address_type: int = field(metadata=AddressType.type_metadata(1))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    connection_interval: int = field(metadata=metadata(2))
    peripheral_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))
    central_clock_accuracy: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Advertising_Report_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.2 LE Advertising Report Event
    '''

    class EventType(SpecableEnum):
        ADV_IND = 0x00
        ADV_DIRECT_IND = 0x01
        ADV_SCAN_IND = 0x02
        ADV_NONCONN_IND = 0x03
        SCAN_RSP = 0x04

    @dataclasses.dataclass
    class Report(HCI_Dataclass_Object):
        event_type: int = field(
            metadata=metadata(
                {
                    'size': 1,
                    'mapper': lambda x: HCI_LE_Advertising_Report_Event.EventType(
                        x
                    ).name,
                }
            )
        )
        address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
        address: Address = field(
            metadata=metadata(Address.parse_address_preceded_by_type)
        )
        data: bytes = field(metadata=metadata('v'))
        rssi: int = field(metadata=metadata(-1))

    reports: Sequence[Report] = field(
        metadata=metadata(Report.parse_from_bytes, list_begin=True, list_end=True)
    )


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Connection_Update_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.3 LE Connection Update Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    connection_interval: int = field(metadata=metadata(2))
    peripheral_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Read_Remote_Features_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.4 LE Read Remote Features Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    le_features: bytes = field(metadata=metadata(8))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Long_Term_Key_Request_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.5 LE Long Term Key Request Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    random_number: bytes = field(metadata=metadata(8))
    encryption_diversifier: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Remote_Connection_Parameter_Request_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.6 LE Remote Connection Parameter Request Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    interval_min: int = field(metadata=metadata(2))
    interval_max: int = field(metadata=metadata(2))
    max_latency: int = field(metadata=metadata(2))
    timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Data_Length_Change_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.7 LE Data Length Change Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    max_tx_octets: int = field(metadata=metadata(2))
    max_tx_time: int = field(metadata=metadata(2))
    max_rx_octets: int = field(metadata=metadata(2))
    max_rx_time: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Enhanced_Connection_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.10 LE Enhanced Connection Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    role: int = field(metadata=Role.type_metadata(1))
    peer_address_type: int = field(metadata=AddressType.type_metadata(1))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    local_resolvable_private_address: Address = field(
        metadata=metadata(Address.parse_random_address)
    )
    peer_resolvable_private_address: Address = field(
        metadata=metadata(Address.parse_random_address)
    )
    connection_interval: int = field(metadata=metadata(2))
    peripheral_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))
    central_clock_accuracy: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Enhanced_Connection_Complete_V2_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.10 LE Enhanced Connection Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    role: int = field(metadata=Role.type_metadata(1))
    peer_address_type: int = field(metadata=AddressType.type_metadata(1))
    peer_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    local_resolvable_private_address: Address = field(
        metadata=metadata(Address.parse_random_address)
    )
    peer_resolvable_private_address: Address = field(
        metadata=metadata(Address.parse_random_address)
    )
    connection_interval: int = field(metadata=metadata(2))
    peripheral_latency: int = field(metadata=metadata(2))
    supervision_timeout: int = field(metadata=metadata(2))
    central_clock_accuracy: int = field(metadata=metadata(1))
    advertising_handle: int = field(metadata=metadata(1))
    sync_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_PHY_Update_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.12 LE PHY Update Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    tx_phy: int = field(metadata=Phy.type_metadata(1))
    rx_phy: int = field(metadata=Phy.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Extended_Advertising_Report_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.13 LE Extended Advertising Report Event
    '''

    class EventType(enum.IntFlag):
        CONNECTABLE_ADVERTISING = 1 << 0
        SCANNABLE_ADVERTISING = 1 << 1
        DIRECTED_ADVERTISING = 1 << 2
        SCAN_RESPONSE = 1 << 3
        LEGACY_ADVERTISING_PDU_USED = 1 << 4

    DATA_COMPLETE = 0x00
    DATA_INCOMPLETE_MORE_TO_COME = 0x01
    DATA_INCOMPLETE_TRUNCATED_NO_MORE_TO_COME = 0x02

    LEGACY_PDU_TYPE_MAP = {
        0b0011: HCI_LE_Advertising_Report_Event.EventType.ADV_IND,
        0b0101: HCI_LE_Advertising_Report_Event.EventType.ADV_DIRECT_IND,
        0b0010: HCI_LE_Advertising_Report_Event.EventType.ADV_SCAN_IND,
        0b0000: HCI_LE_Advertising_Report_Event.EventType.ADV_NONCONN_IND,
        0b1011: HCI_LE_Advertising_Report_Event.EventType.SCAN_RSP,
        0b1010: HCI_LE_Advertising_Report_Event.EventType.SCAN_RSP,
    }

    NO_ADI_FIELD_PROVIDED = 0xFF
    TX_POWER_INFORMATION_NOT_AVAILABLE = 0x7F
    RSSI_NOT_AVAILABLE = 0x7F
    ANONYMOUS_ADDRESS_TYPE = 0xFF
    UNRESOLVED_RESOLVABLE_ADDRESS_TYPE = 0xFE

    @dataclasses.dataclass
    class Report(HCI_Dataclass_Object):
        event_type: int = field(
            metadata=metadata(
                {
                    'size': 2,
                    'mapper': lambda x: HCI_LE_Extended_Advertising_Report_Event.EventType(
                        x
                    ).name,
                }
            )
        )
        address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
        address: Address = field(
            metadata=metadata(Address.parse_address_preceded_by_type)
        )
        primary_phy: int = field(metadata=metadata(Phy.type_spec(1)))
        secondary_phy: int = field(metadata=metadata(Phy.type_spec(1)))
        advertising_sid: int = field(metadata=metadata(1))
        tx_power: int = field(metadata=metadata(1))
        rssi: int = field(metadata=metadata(-1))
        periodic_advertising_interval: int = field(metadata=metadata(2))
        direct_address_type: int = field(metadata=metadata(Address.ADDRESS_TYPE_SPEC))
        direct_address: Address = field(
            metadata=metadata(Address.parse_address_preceded_by_type)
        )
        data: bytes = field(metadata=metadata('v'))

    reports: Sequence[Report] = field(
        metadata=metadata(Report.parse_from_bytes, list_begin=True, list_end=True)
    )


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Established_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.14 LE Periodic Advertising Sync Established Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    sync_handle: int = field(metadata=metadata(2))
    advertising_sid: int = field(metadata=metadata(1))
    advertiser_address_type: int = field(metadata=AddressType.type_metadata(1))
    advertiser_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertiser_phy: int = field(metadata=Phy.type_metadata(1))
    periodic_advertising_interval: int = field(metadata=metadata(2))
    advertiser_clock_accuracy: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Established_V2_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.14 LE Periodic Advertising Sync Established Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    sync_handle: int = field(metadata=metadata(2))
    advertising_sid: int = field(metadata=metadata(1))
    advertiser_address_type: int = field(metadata=AddressType.type_metadata(1))
    advertiser_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertiser_phy: int = field(metadata=Phy.type_metadata(1))
    periodic_advertising_interval: int = field(metadata=metadata(2))
    advertiser_clock_accuracy: int = field(metadata=metadata(1))
    num_subevents: int = field(metadata=metadata(1))
    subevent_interval: int = field(metadata=metadata(1))
    response_slot_delay: int = field(metadata=metadata(1))
    response_slot_spacing: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Report_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.15 LE Periodic Advertising Report Event
    '''

    class DataStatus(SpecableEnum):
        DATA_COMPLETE = 0x00
        DATA_INCOMPLETE_MORE_TO_COME = 0x01
        DATA_INCOMPLETE_TRUNCATED_NO_MORE_TO_COME = 0x02

    TX_POWER_INFORMATION_NOT_AVAILABLE = 0x7F
    RSSI_NOT_AVAILABLE = 0x7F

    sync_handle: int = field(metadata=metadata(2))
    tx_power: int = field(metadata=metadata(-1))
    rssi: int = field(metadata=metadata(-1))
    cte_type: int = field(metadata=CteType.type_metadata(1))
    data_status: int = field(metadata=DataStatus.type_metadata(1))
    data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Report_V2_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.15 LE Periodic Advertising Report Event
    '''

    sync_handle: int = field(metadata=metadata(2))
    tx_power: int = field(metadata=metadata(-1))
    rssi: int = field(metadata=metadata(-1))
    cte_type: int = field(metadata=CteType.type_metadata(1))
    periodic_event_counter: int = field(metadata=metadata(2))
    subevent: int = field(metadata=metadata(1))
    data_status: int = field(metadata=CteType.type_metadata(1))
    data: bytes = field(metadata=metadata("v"))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Lost_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.16 LE Periodic Advertising Sync Lost Event
    '''

    sync_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Advertising_Set_Terminated_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.18 LE Advertising Set Terminated Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    advertising_handle: int = field(metadata=metadata(1))
    connection_handle: int = field(metadata=metadata(2))
    num_completed_extended_advertising_events: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Channel_Selection_Algorithm_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.20 LE Channel Selection Algorithm Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    channel_selection_algorithm: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Transfer_Received_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.24 LE Periodic Advertising Sync Transfer Received Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    service_data: int = field(metadata=metadata(2))
    sync_handle: int = field(metadata=metadata(2))
    advertising_sid: int = field(metadata=metadata(1))
    advertiser_address_type: int = field(metadata=AddressType.type_metadata(1))
    advertiser_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertiser_phy: int = field(metadata=metadata(1))
    periodic_advertising_interval: int = field(metadata=metadata(2))
    advertiser_clock_accuracy: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Periodic_Advertising_Sync_Transfer_Received_V2_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.24 LE Periodic Advertising Sync Transfer Received Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    service_data: int = field(metadata=metadata(2))
    sync_handle: int = field(metadata=metadata(2))
    advertising_sid: int = field(metadata=metadata(1))
    advertiser_address_type: int = field(metadata=AddressType.type_metadata(1))
    advertiser_address: Address = field(
        metadata=metadata(Address.parse_address_preceded_by_type)
    )
    advertiser_phy: int = field(metadata=metadata(1))
    periodic_advertising_interval: int = field(metadata=metadata(2))
    advertiser_clock_accuracy: int = field(metadata=metadata(1))
    num_subevents: int = field(metadata=metadata(1))
    subevent_interval: int = field(metadata=metadata(1))
    response_slot_delay: int = field(metadata=metadata(1))
    response_slot_spacing: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CIS_Established_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.25 LE CIS Established Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    cig_sync_delay: int = field(metadata=metadata(3))
    cis_sync_delay: int = field(metadata=metadata(3))
    transport_latency_c_to_p: int = field(metadata=metadata(3))
    transport_latency_p_to_c: int = field(metadata=metadata(3))
    phy_c_to_p: int = field(metadata=metadata(1))
    phy_p_to_c: int = field(metadata=metadata(1))
    nse: int = field(metadata=metadata(1))
    bn_c_to_p: int = field(metadata=metadata(1))
    bn_p_to_c: int = field(metadata=metadata(1))
    ft_c_to_p: int = field(metadata=metadata(1))
    ft_p_to_c: int = field(metadata=metadata(1))
    max_pdu_c_to_p: int = field(metadata=metadata(2))
    max_pdu_p_to_c: int = field(metadata=metadata(2))
    iso_interval: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CIS_Request_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.26 LE CIS Request Event
    '''

    acl_connection_handle: int = field(metadata=metadata(2))
    cis_connection_handle: int = field(metadata=metadata(2))
    cig_id: int = field(metadata=metadata(1))
    cis_id: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Create_BIG_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.27 LE Create BIG Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    big_handle: int = field(metadata=metadata(1))
    big_sync_delay: int = field(metadata=metadata(3))
    transport_latency_big: int = field(metadata=metadata(3))
    phy: int = field(metadata=metadata(1))
    nse: int = field(metadata=metadata(1))
    bn: int = field(metadata=metadata(1))
    pto: int = field(metadata=metadata(1))
    irc: int = field(metadata=metadata(1))
    max_pdu: int = field(metadata=metadata(2))
    iso_interval: int = field(metadata=metadata(2))
    connection_handle: int = field(metadata=metadata(2, list_begin=True, list_end=True))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_Terminate_BIG_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.28 LE Terminate BIG Complete Event
    '''

    big_handle: int = field(metadata=metadata(1))
    reason: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_BIG_Sync_Established_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.29 LE BIG Sync Established event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    big_handle: int = field(metadata=metadata(1))
    transport_latency_big: int = field(metadata=metadata(3))
    nse: int = field(metadata=metadata(1))
    bn: int = field(metadata=metadata(1))
    pto: int = field(metadata=metadata(1))
    irc: int = field(metadata=metadata(1))
    max_pdu: int = field(metadata=metadata(2))
    iso_interval: int = field(metadata=metadata(2))
    connection_handle: int = field(metadata=metadata(2, list_begin=True, list_end=True))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_BIG_Sync_Lost_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.30 LE BIG Sync Lost event
    '''

    big_handle: int = field(metadata=metadata(1))
    reason: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_BIGInfo_Advertising_Report_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.34 LE BIGInfo Advertising Report Event
    '''

    sync_handle: int = field(metadata=metadata(2))
    num_bis: int = field(metadata=metadata(1))
    nse: int = field(metadata=metadata(1))
    iso_interval: int = field(metadata=metadata(2))
    bn: int = field(metadata=metadata(1))
    pto: int = field(metadata=metadata(1))
    irc: int = field(metadata=metadata(1))
    max_pdu: int = field(metadata=metadata(2))
    sdu_interval: int = field(metadata=metadata(3))
    max_sdu: int = field(metadata=metadata(2))
    phy: int = field(metadata=Phy.type_metadata(1))
    framing: int = field(metadata=metadata(1))
    encryption: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Read_Remote_Supported_Capabilities_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.39 LE CS Read Remote Supported Capabilities Complete event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    num_config_supported: int = field(metadata=metadata(1))
    max_consecutive_procedures_supported: int = field(metadata=metadata(2))
    num_antennas_supported: int = field(metadata=metadata(1))
    max_antenna_paths_supported: int = field(metadata=metadata(1))
    roles_supported: int = field(metadata=metadata(1))
    modes_supported: int = field(metadata=metadata(1))
    rtt_capability: int = field(metadata=metadata(1))
    rtt_aa_only_n: int = field(metadata=metadata(1))
    rtt_sounding_n: int = field(metadata=metadata(1))
    rtt_random_payload_n: int = field(metadata=metadata(1))
    nadm_sounding_capability: int = field(metadata=metadata(2))
    nadm_random_capability: int = field(metadata=metadata(2))
    cs_sync_phys_supported: int = field(metadata=metadata(CS_SYNC_PHY_SUPPORTED_SPEC))
    subfeatures_supported: int = field(metadata=metadata(2))
    t_ip1_times_supported: int = field(metadata=metadata(2))
    t_ip2_times_supported: int = field(metadata=metadata(2))
    t_fcs_times_supported: int = field(metadata=metadata(2))
    t_pm_times_supported: int = field(metadata=metadata(2))
    t_sw_time_supported: int = field(metadata=metadata(1))
    tx_snr_capability: int = field(metadata=metadata(CS_SNR_SPEC))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Read_Remote_FAE_Table_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.40 LE CS Read Remote FAE Table Complete event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    remote_fae_table: bytes = field(metadata=metadata(72))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Security_Enable_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.41 LE CS Security Enable Complete event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Config_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.42 LE CS Config Complete event
    '''

    class Action(SpecableEnum):
        REMOVED = 0
        CREATED = 1

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    action: int = field(metadata=Action.type_metadata(1))
    main_mode_type: int = field(metadata=metadata(1))
    sub_mode_type: int = field(metadata=metadata(1))
    min_main_mode_steps: int = field(metadata=metadata(1))
    max_main_mode_steps: int = field(metadata=metadata(1))
    main_mode_repetition: int = field(metadata=metadata(1))
    mode_0_steps: int = field(metadata=metadata(1))
    role: int = field(metadata=metadata(CS_ROLE_SPEC))
    rtt_type: int = field(metadata=metadata(RTT_TYPE_SPEC))
    cs_sync_phy: int = field(metadata=metadata(CS_SYNC_PHY_SPEC))
    channel_map: bytes = field(metadata=metadata(10))
    channel_map_repetition: int = field(metadata=metadata(1))
    channel_selection_type: int = field(metadata=metadata(1))
    ch3c_shape: int = field(metadata=metadata(1))
    ch3c_jump: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))
    t_ip1_time: int = field(metadata=metadata(1))
    t_ip2_time: int = field(metadata=metadata(1))
    t_fcs_time: int = field(metadata=metadata(1))
    t_pm_time: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Procedure_Enable_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.43 LE CS Procedure Enable Complete event
    '''

    class State(SpecableEnum):
        DISABLED = 0
        ENABLED = 1

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    state: int = field(metadata=State.type_metadata(1))
    tone_antenna_config_selection: int = field(metadata=metadata(1))
    selected_tx_power: int = field(metadata=metadata(-1))
    subevent_len: int = field(metadata=metadata(3))
    subevents_per_event: int = field(metadata=metadata(1))
    subevent_interval: int = field(metadata=metadata(2))
    event_interval: int = field(metadata=metadata(2))
    procedure_interval: int = field(metadata=metadata(2))
    procedure_count: int = field(metadata=metadata(2))
    max_procedure_len: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Subevent_Result_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.44 LE CS Subevent Result event
    '''

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    start_acl_conn_event_counter: int = field(metadata=metadata(2))
    procedure_counter: int = field(metadata=metadata(2))
    frequency_compensation: int = field(metadata=metadata(2))
    reference_power_level: int = field(metadata=metadata(-1))
    procedure_done_status: int = field(metadata=metadata(1))
    subevent_done_status: int = field(metadata=metadata(1))
    abort_reason: int = field(metadata=metadata(1))
    num_antenna_paths: int = field(metadata=metadata(1))
    step_mode: Sequence[int] = field(metadata=metadata(1, list_begin=True))
    step_channel: Sequence[int] = field(metadata=metadata(1))
    step_data: Sequence[bytes] = field(metadata=metadata("v", list_end=True))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Subevent_Result_Continue_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.45 LE CS Subevent Result Continue event
    '''

    connection_handle: int = field(metadata=metadata(2))
    config_id: int = field(metadata=metadata(1))
    procedure_done_status: int = field(metadata=metadata(1))
    subevent_done_status: int = field(metadata=metadata(1))
    abort_reason: int = field(metadata=metadata(1))
    num_antenna_paths: int = field(metadata=metadata(1))
    step_mode: Sequence[int] = field(metadata=metadata(1, list_begin=True))
    step_channel: Sequence[int] = field(metadata=metadata(1))
    step_data: Sequence[bytes] = field(metadata=metadata("v", list_end=True))


# -----------------------------------------------------------------------------
@HCI_LE_Meta_Event.event
@dataclasses.dataclass
class HCI_LE_CS_Test_End_Complete_Event(HCI_LE_Meta_Event):
    '''
    See Bluetooth spec @ 7.7.65.46 LE CS Test End Complete event
    '''

    connection_handle: int = field(metadata=metadata(2))
    status: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Inquiry_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.1 Inquiry Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Inquiry_Result_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.2 Inquiry Result Event
    '''

    bd_addr: Sequence[Address] = field(
        metadata=metadata(Address.parse_address, list_begin=True)
    )
    page_scan_repetition_mode: Sequence[int] = field(metadata=metadata(1))
    reserved_0: Sequence[int] = field(metadata=metadata(1))
    reserved_1: Sequence[int] = field(metadata=metadata(1))
    class_of_device: Sequence[int] = field(metadata=metadata(COD_SPEC))
    clock_offset: Sequence[int] = field(metadata=metadata(2, list_end=True))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Connection_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.3 Connection Complete Event
    '''

    class LinkType(SpecableEnum):
        SCO = 0x00
        ACL = 0x01
        ESCO = 0x02

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    link_type: int = field(metadata=LinkType.type_metadata(1))
    encryption_enabled: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Connection_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.4 Connection Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    class_of_device: int = field(metadata=metadata(3))
    link_type: int = field(
        metadata=HCI_Connection_Complete_Event.LinkType.type_metadata(1)
    )


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Disconnection_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.5 Disconnection Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    reason: int = field(metadata=metadata(STATUS_SPEC))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Authentication_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.6 Authentication Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Remote_Name_Request_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.7 Remote Name Request Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    remote_name: bytes = field(
        metadata=metadata({'size': 248, 'mapper': map_null_terminated_utf8_string})
    )


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Encryption_Change_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.8 Encryption Change Event
    '''

    class Enabled(SpecableEnum):
        OFF = 0x00
        E0_OR_AES_CCM = 0x01
        AES_CCM = 0x02

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    encryption_enabled: int = field(metadata=Enabled.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Encryption_Change_V2_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.8 Encryption Change Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    encryption_enabled: int = field(
        metadata=HCI_Encryption_Change_Event.Enabled.type_metadata(1)
    )
    encryption_key_size: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Read_Remote_Supported_Features_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.11 Read Remote Supported Features Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    lmp_features: bytes = field(metadata=metadata(8))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Read_Remote_Version_Information_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.12 Read Remote Version Information Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    version: int = field(metadata=metadata(1))
    manufacturer_name: int = field(metadata=metadata(2))
    subversion: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_QOS_Setup_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.13 QoS Setup Complete Event
    '''

    class ServiceType(SpecableEnum):
        NO_TRAFFIC_AVAILABLE = 0x00
        BEST_EFFORT_AVAILABLE = 0x01
        GUARANTEED_AVAILABLE = 0x02

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    unused: int = field(metadata=metadata(1))
    service_type: int = field(metadata=ServiceType.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Command_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.14 Command Complete Event
    '''

    num_hci_command_packets: int = field(metadata=metadata(1))
    command_opcode: int = field(
        metadata=metadata({'size': 2, 'mapper': HCI_Command.command_name})
    )
    return_parameters: Union[bytes, HCI_Object, int] = field(metadata=metadata("*"))

    def map_return_parameters(self, return_parameters):
        '''Map simple 'status' return parameters to their named constant form'''

        if isinstance(return_parameters, bytes) and len(return_parameters) == 1:
            # Byte-array form
            return HCI_Constant.status_name(return_parameters[0])

        if isinstance(return_parameters, int):
            # Already converted to an integer status code
            return HCI_Constant.status_name(return_parameters)

        return return_parameters

    @classmethod
    def from_parameters(cls, parameters: bytes) -> Self:
        event = cls(**HCI_Object.dict_from_bytes(parameters, 0, cls.fields))
        event.parameters = parameters
        # Parse the return parameters
        if (
            isinstance(event.return_parameters, bytes)
            and len(event.return_parameters) == 1
        ):
            # All commands with 1-byte return parameters return a 'status' field,
            # convert it to an integer
            event.return_parameters = event.return_parameters[0]
        else:
            subclass = HCI_Command.command_classes.get(event.command_opcode)
            if subclass:
                # Try to parse the return parameters bytes into an object.
                return_parameters = subclass.parse_return_parameters(
                    event.return_parameters
                )
                if return_parameters is not None:
                    event.return_parameters = return_parameters

        return event

    def __str__(self):
        return f'{color(self.name, "magenta")}:\n' + HCI_Object.format_fields(
            self.__dict__,
            self.fields,
            '  ',
            {'return_parameters': self.map_return_parameters},
        )


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Command_Status_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.15 Command Complete Event
    '''

    PENDING = 0

    @staticmethod
    def status_name(status):
        if status == HCI_Command_Status_Event.PENDING:
            return 'PENDING'

        return HCI_Constant.error_name(status)

    status: int = field(
        metadata=metadata(
            {'size': 1, 'mapper': lambda x: HCI_Command_Status_Event.status_name(x)}
        )
    )
    num_hci_command_packets: int = field(metadata=metadata(1))
    command_opcode: int = field(
        metadata=metadata({'size': 2, 'mapper': HCI_Command.command_name})
    )


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Role_Change_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.18 Role Change Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    new_role: int = field(metadata=Role.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Number_Of_Completed_Packets_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.19 Number Of Completed Packets Event
    '''

    connection_handles: Sequence[int] = field(metadata=metadata(2, list_begin=True))
    num_completed_packets: Sequence[int] = field(metadata=metadata(2, list_end=True))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Mode_Change_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.20 Mode Change Event
    '''

    class Mode(SpecableEnum):
        ACTIVE = 0x00
        HOLD = 0x01
        SNIFF = 0x02

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    current_mode: int = field(metadata=Mode.type_metadata(1))
    interval: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_PIN_Code_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.22 PIN Code Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Link_Key_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.24 7.7.23 Link Key Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Link_Key_Notification_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.24 Link Key Notification Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    link_key: bytes = field(metadata=metadata(16))
    key_type: int = field(metadata=LinkKeyType.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Max_Slots_Change_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.27 Max Slots Change Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    lmp_max_slots: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Read_Clock_Offset_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.28 Read Clock Offset Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    clock_offset: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Connection_Packet_Type_Changed_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.29 Connection Packet Type Changed Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    packet_type: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Page_Scan_Repetition_Mode_Change_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.31 Page Scan Repetition Mode Change Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    page_scan_repetition_mode: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Inquiry_Result_With_RSSI_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.33 Inquiry Result with RSSI Event
    '''

    bd_addr: Sequence[Address] = field(
        metadata=metadata(Address.parse_address, list_begin=True)
    )
    page_scan_repetition_mode: Sequence[int] = field(metadata=metadata(1))
    reserved: Sequence[int] = field(metadata=metadata(1))
    class_of_device: Sequence[int] = field(metadata=metadata(COD_SPEC))
    clock_offset: Sequence[int] = field(metadata=metadata(2))
    rssi: Sequence[int] = field(metadata=metadata(-1, list_end=True))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Read_Remote_Extended_Features_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.34 Read Remote Extended Features Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    page_number: int = field(metadata=metadata(1))
    maximum_page_number: int = field(metadata=metadata(1))
    extended_lmp_features: bytes = field(metadata=metadata(8))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Synchronous_Connection_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.35 Synchronous Connection Complete Event
    '''

    class LinkType(SpecableEnum):
        SCO = 0x00
        ESCO = 0x02

    class AirMode(SpecableEnum):
        U_LAW_LOG = 0x00
        A_LAW_LOG_AIR_MORE = 0x01
        CVSD = 0x02
        TRANSPARENT_DATA = 0x03

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    link_type: int = field(metadata=LinkType.type_metadata(1))
    transmission_interval: int = field(metadata=metadata(1))
    retransmission_window: int = field(metadata=metadata(1))
    rx_packet_length: int = field(metadata=metadata(2))
    tx_packet_length: int = field(metadata=metadata(2))
    air_mode: int = field(metadata=AirMode.type_metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Synchronous_Connection_Changed_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.36 Synchronous Connection Changed Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    transmission_interval: int = field(metadata=metadata(1))
    retransmission_window: int = field(metadata=metadata(1))
    rx_packet_length: int = field(metadata=metadata(2))
    tx_packet_length: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Sniff_Subrating_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.37 Sniff Subrating Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))
    max_tx_latency: int = field(metadata=metadata(2))
    max_rx_latency: int = field(metadata=metadata(2))
    min_remote_timeout: int = field(metadata=metadata(2))
    min_local_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Extended_Inquiry_Result_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.38 Extended Inquiry Result Event
    '''

    num_responses: int = field(metadata=metadata(1))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    page_scan_repetition_mode: int = field(metadata=metadata(1))
    reserved: int = field(metadata=metadata(1))
    class_of_device: int = field(metadata=metadata(COD_SPEC))
    clock_offset: int = field(metadata=metadata(2))
    rssi: int = field(metadata=metadata(-1))
    extended_inquiry_response: bytes = field(metadata=metadata(240))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Encryption_Key_Refresh_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.39 Encryption Key Refresh Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    connection_handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_IO_Capability_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.40 IO Capability Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_IO_Capability_Response_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.41 IO Capability Response Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    io_capability: int = field(metadata=IoCapability.type_metadata(1))
    oob_data_present: int = field(metadata=metadata(1))
    authentication_requirements: int = field(
        metadata=AuthenticationRequirements.type_metadata(1)
    )


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_User_Confirmation_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.42 User Confirmation Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    numeric_value: int = field(metadata=metadata(4))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_User_Passkey_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.43 User Passkey Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Remote_OOB_Data_Request_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.44 Remote OOB Data Request Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Simple_Pairing_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.45 Simple Pairing Complete Event
    '''

    status: int = field(metadata=metadata(STATUS_SPEC))
    bd_addr: Address = field(metadata=metadata(Address.parse_address))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Link_Supervision_Timeout_Changed_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.46 Link Supervision Timeout Changed Event
    '''

    connection_handle: int = field(metadata=metadata(2))
    link_supervision_timeout: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Enhanced_Flush_Complete_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.47 Enhanced Flush Complete Event
    '''

    handle: int = field(metadata=metadata(2))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_User_Passkey_Notification_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.48 User Passkey Notification Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    passkey: int = field(metadata=metadata(4))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Keypress_Notification_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.49 Keypress Notification Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    notification_type: int = field(metadata=metadata(1))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Remote_Host_Supported_Features_Notification_Event(HCI_Event):
    '''
    See Bluetooth spec @ 7.7.50 Remote Host Supported Features Notification Event
    '''

    bd_addr: Address = field(metadata=metadata(Address.parse_address))
    host_supported_features: bytes = field(metadata=metadata(8))


# -----------------------------------------------------------------------------
@HCI_Event.event
@dataclasses.dataclass
class HCI_Vendor_Event(HCI_Event):
    '''
    See Bluetooth spec @ 5.4.4 HCI Event packet
    '''

    data: bytes = field(metadata=metadata("*"))


# -----------------------------------------------------------------------------
class HCI_AclDataPacket(HCI_Packet):
    '''
    See Bluetooth spec @ 5.4.2 HCI ACL Data Packets
    '''

    hci_packet_type = HCI_ACL_DATA_PACKET

    @staticmethod
    def from_bytes(packet: bytes) -> HCI_AclDataPacket:
        # Read the header
        h, data_total_length = struct.unpack_from('<HH', packet, 1)
        connection_handle = h & 0xFFF
        pb_flag = (h >> 12) & 3
        bc_flag = (h >> 14) & 3
        data = packet[5:]
        if len(data) != data_total_length:
            raise InvalidPacketError('invalid packet length')
        return HCI_AclDataPacket(
            connection_handle, pb_flag, bc_flag, data_total_length, data
        )

    def __bytes__(self):
        h = (self.pb_flag << 12) | (self.bc_flag << 14) | self.connection_handle
        return (
            struct.pack('<BHH', HCI_ACL_DATA_PACKET, h, self.data_total_length)
            + self.data
        )

    def __init__(self, connection_handle, pb_flag, bc_flag, data_total_length, data):
        self.connection_handle = connection_handle
        self.pb_flag = pb_flag
        self.bc_flag = bc_flag
        self.data_total_length = data_total_length
        self.data = data

    def __str__(self):
        return (
            f'{color("ACL", "blue")}: '
            f'handle=0x{self.connection_handle:04x}, '
            f'pb={self.pb_flag}, bc={self.bc_flag}, '
            f'data_total_length={self.data_total_length}, '
            f'data={self.data.hex()}'
        )


# -----------------------------------------------------------------------------
class HCI_SynchronousDataPacket(HCI_Packet):
    '''
    See Bluetooth spec @ 5.4.3 HCI SCO Data Packets
    '''

    hci_packet_type = HCI_SYNCHRONOUS_DATA_PACKET

    @staticmethod
    def from_bytes(packet: bytes) -> HCI_SynchronousDataPacket:
        # Read the header
        h, data_total_length = struct.unpack_from('<HB', packet, 1)
        connection_handle = h & 0xFFF
        packet_status = (h >> 12) & 0b11
        data = packet[4:]
        if len(data) != data_total_length:
            raise InvalidPacketError(
                f'invalid packet length {len(data)} != {data_total_length}'
            )
        return HCI_SynchronousDataPacket(
            connection_handle, packet_status, data_total_length, data
        )

    def __bytes__(self) -> bytes:
        h = (self.packet_status << 12) | self.connection_handle
        return (
            struct.pack('<BHB', HCI_SYNCHRONOUS_DATA_PACKET, h, self.data_total_length)
            + self.data
        )

    def __init__(
        self,
        connection_handle: int,
        packet_status: int,
        data_total_length: int,
        data: bytes,
    ) -> None:
        self.connection_handle = connection_handle
        self.packet_status = packet_status
        self.data_total_length = data_total_length
        self.data = data

    def __str__(self) -> str:
        return (
            f'{color("SCO", "blue")}: '
            f'handle=0x{self.connection_handle:04x}, '
            f'ps={self.packet_status}, '
            f'data_total_length={self.data_total_length}, '
            f'data={self.data.hex()}'
        )


# -----------------------------------------------------------------------------
@dataclasses.dataclass
class HCI_IsoDataPacket(HCI_Packet):
    '''
    See Bluetooth spec @ 5.4.5 HCI ISO Data Packets
    '''

    hci_packet_type: ClassVar[int] = HCI_ISO_DATA_PACKET

    connection_handle: int
    data_total_length: int
    iso_sdu_fragment: bytes
    pb_flag: int
    ts_flag: int = 0
    time_stamp: Optional[int] = None
    packet_sequence_number: Optional[int] = None
    iso_sdu_length: Optional[int] = None
    packet_status_flag: Optional[int] = None

    def __post_init__(self) -> None:
        self.ts_flag = self.time_stamp is not None

    @staticmethod
    def from_bytes(packet: bytes) -> HCI_IsoDataPacket:
        time_stamp: Optional[int] = None
        packet_sequence_number: Optional[int] = None
        iso_sdu_length: Optional[int] = None
        packet_status_flag: Optional[int] = None

        pos = 1
        pdu_info, data_total_length = struct.unpack_from('<HH', packet, pos)
        connection_handle = pdu_info & 0xFFF
        pb_flag = (pdu_info >> 12) & 0b11
        ts_flag = (pdu_info >> 14) & 0b01
        pos += 4

        # pb_flag in (0b00, 0b10) but faster
        should_include_sdu_info = not (pb_flag & 0b01)

        if ts_flag:
            if not should_include_sdu_info:
                logger.warning(f'Timestamp included when pb_flag={bin(pb_flag)}')
            time_stamp, *_ = struct.unpack_from('<I', packet, pos)
            pos += 4

        if should_include_sdu_info:
            packet_sequence_number, sdu_info = struct.unpack_from('<HH', packet, pos)
            iso_sdu_length = sdu_info & 0xFFF
            packet_status_flag = (sdu_info >> 15) & 1
            pos += 4

        iso_sdu_fragment = packet[pos:]
        return HCI_IsoDataPacket(
            connection_handle=connection_handle,
            pb_flag=pb_flag,
            ts_flag=ts_flag,
            data_total_length=data_total_length,
            time_stamp=time_stamp,
            packet_sequence_number=packet_sequence_number,
            iso_sdu_length=iso_sdu_length,
            packet_status_flag=packet_status_flag,
            iso_sdu_fragment=iso_sdu_fragment,
        )

    def __bytes__(self) -> bytes:
        fmt = '<BHH'
        args = [
            HCI_ISO_DATA_PACKET,
            self.ts_flag << 14 | self.pb_flag << 12 | self.connection_handle,
            self.data_total_length,
        ]
        if self.time_stamp is not None:
            fmt += 'I'
            args.append(self.time_stamp)
        if (
            self.packet_sequence_number is not None
            and self.iso_sdu_length is not None
            and self.packet_status_flag is not None
        ):
            fmt += 'HH'
            args += [
                self.packet_sequence_number,
                self.iso_sdu_length | self.packet_status_flag << 15,
            ]
        return struct.pack(fmt, *args) + self.iso_sdu_fragment

    def __str__(self) -> str:
        result = (
            f'{color("ISO", "blue")}: '
            f'handle=0x{self.connection_handle:04x}, '
            f'pb={self.pb_flag}, '
            f'data_total_length={self.data_total_length}'
        )

        if self.ts_flag:
            result += f', time_stamp={self.time_stamp}'

        if self.pb_flag in (0b00, 0b10):
            result += (
                ', '
                f'packet_sequence_number={self.packet_sequence_number}, '
                f'ps={self.packet_status_flag}, '
                f'sdu_fragment={self.iso_sdu_fragment.hex()}'
            )

        return result


# -----------------------------------------------------------------------------
class HCI_AclDataPacketAssembler:
    current_data: Optional[bytes]

    def __init__(self, callback: Callable[[bytes], Any]) -> None:
        self.callback = callback
        self.current_data = None
        self.l2cap_pdu_length = 0

    def feed_packet(self, packet: HCI_AclDataPacket) -> None:
        if packet.pb_flag in (
            HCI_ACL_PB_FIRST_NON_FLUSHABLE,
            HCI_ACL_PB_FIRST_FLUSHABLE,
        ):
            (l2cap_pdu_length,) = struct.unpack_from('<H', packet.data, 0)
            self.current_data = packet.data
            self.l2cap_pdu_length = l2cap_pdu_length
        elif packet.pb_flag == HCI_ACL_PB_CONTINUATION:
            if self.current_data is None:
                logger.warning('!!! ACL continuation without start')
                return
            self.current_data += packet.data

        assert self.current_data is not None
        if len(self.current_data) == self.l2cap_pdu_length + 4:
            # The packet is complete, invoke the callback
            logger.debug(f'<<< ACL PDU: {self.current_data.hex()}')
            self.callback(self.current_data)

            # Reset
            self.current_data = None
            self.l2cap_pdu_length = 0
        else:
            # Compliance check
            if len(self.current_data) > self.l2cap_pdu_length + 4:
                logger.warning('!!! ACL data exceeds L2CAP PDU')
                self.current_data = None
                self.l2cap_pdu_length = 0
