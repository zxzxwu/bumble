# Copyright 2021-2024 Google LLC
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
import enum

from bumble import core
from bumble import gatt
from bumble import gatt_client
from bumble import utils

from typing import Optional, ClassVar, Dict

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------


class StatusFlags(enum.IntFlag):
    '''
    See Telephone Bearer Service, 3.9. Status Flags.
    '''

    INBAND_RINGTONE = 0x01
    SILENT_MODE = 0x2

    @property
    def is_inband_ringtone_enabled(self) -> bool:
        return (self & self.INBAND_RINGTONE) != 0

    @property
    def is_in_silent_mode(self) -> bool:
        return (self & self.SILENT_MODE) != 0


class CallState(enum.IntEnum):
    '''
    See Telephone Bearer Service, 3.11. Call State.
    '''

    INCOMING = 0x00
    DIALING = 0x01
    ALERTING = 0x02
    ACTIVE = 0x03
    LOCALLY_HELD = 0x04
    REMOTELY_HELD = 0x05
    LOCALLY_AND_REMOTELY_HELD = 0x06


class CallFlags(enum.IntFlag):
    '''
    See Telephone Bearer Service, 3.11. Call State.
    '''

    IS_OUTGOING = 0x01
    INFO_WITHHELD_BY_SERVER = 0x02
    INFO_WITHHELD_BY_NETWORK = 0x04

    @property
    def is_outgoing(self) -> bool:
        return (self & self.IS_OUTGOING) != 0

    @property
    def is_info_withheld_by_server(self) -> bool:
        return (self & self.INFO_WITHHELD_BY_SERVER) != 0

    @property
    def is_info_withheld_by_network(self) -> bool:
        return (self & self.INFO_WITHHELD_BY_NETWORK) != 0


class CallControlPointOpcode(enum.IntEnum):
    '''
    See Telephone Bearer Service, 3.12. Call Control Point.
    '''

    ACCEPT = 0x00
    TERMINATE = 0x01
    LOCAL_HOLD = 0x02
    LOCAL_RETRIEVE = 0x03
    ORIGINATE = 0x04
    JOIN = 0x05


class CallControlPointResultCode(enum.IntEnum):
    '''
    See Telephone Bearer Service, 3.12.2. Call Control Point Notification.
    '''

    SUCCESS = 0x00
    OPCODE_NOT_SUPPORTED = 0x01
    OPERATION_NOT_POSSIBLE = 0x02
    INVALID_CALL_INDEX = 0x03
    STATE_MISMATCH = 0x04
    LACK_OF_RESOURCES = 0x05
    INVALID_OUTGOING_URI = 0x06


class CallControlPointOptionalOpcode(enum.IntFlag):
    '''
    See Telephone Bearer Service, 3.13. Call Control Point Optional Opcodes.
    '''

    LOCAL_HOLD = 0x01
    JOIN = 0x02


class TerminationReason(enum.IntEnum):
    '''
    See Telephone Bearer Service, 3.14. Termination Reason.
    '''

    IMPROPER_URI = 0x00
    CALL_FAILED = 0x01
    REMOTE_PARTY_ENDED_THE_CALL = 0x02
    CALL_ENDED_FROM_SERVER = 0x03
    LINE_BUSY = 0x04
    NETWORK_CONGESTION = 0x05
    CLIENT_TERMINATED_THE_CALL = 0x06
    NO_SERVICE = 0x07
    NO_ANSWER = 0x08
    UNSPECIFIED = 0x09


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------
class TelephoneBearerService(gatt.TemplateService):
    UUID = gatt.GATT_TELEPHONE_BEARER_SERVICE


# -----------------------------------------------------------------------------
# Client
# -----------------------------------------------------------------------------
class TelephoneBearerServiceProxy(
    gatt_client.ProfileServiceProxy, utils.CompositeEventEmitter
):
    SERVICE_CLASS = TelephoneBearerService

    bearer_provider_name: Optional[gatt_client.CharacteristicProxy] = None
    bearer_uniform_caller_identifier: Optional[gatt_client.CharacteristicProxy] = None
    bearer_technology: Optional[gatt_client.CharacteristicProxy] = None
    bearer_uri_schemes_supported_list: Optional[gatt_client.CharacteristicProxy] = None
    bearer_signal_strength: Optional[gatt_client.CharacteristicProxy] = None
    bearer_signal_strength_reporting_interval: Optional[
        gatt_client.CharacteristicProxy
    ] = None
    bearer_list_current_calls: Optional[gatt_client.CharacteristicProxy] = None
    content_control_id: Optional[gatt_client.CharacteristicProxy] = None
    status_flags: Optional[gatt_client.CharacteristicProxy] = None
    incoming_call_target_bearer_uri: Optional[gatt_client.CharacteristicProxy] = None
    call_state: Optional[gatt_client.CharacteristicProxy] = None
    call_control_point: Optional[gatt_client.CharacteristicProxy] = None
    call_control_point_optional_opcodes: Optional[gatt_client.CharacteristicProxy] = (
        None
    )
    termination_reason: Optional[gatt_client.CharacteristicProxy] = None
    incoming_call: Optional[gatt_client.CharacteristicProxy] = None
    call_friendly_name: Optional[gatt_client.CharacteristicProxy] = None

    _CHARACTERISTICS: ClassVar[Dict[str, core.UUID]] = {
        'bearer_provider_name': gatt.GATT_BEARER_PROVIDER_NAME_CHARACTERISTIC,
        'bearer_uniform_caller_identifier': gatt.GATT_BEARER_UCI_CHARACTERISTIC,
        'bearer_technology': gatt.GATT_BEARER_TECHNOLOGY_CHARACTERISTIC,
        'bearer_uri_schemes_supported_list': gatt.GATT_BEARER_URI_SCHEMES_SUPPORTED_LIST_CHARACTERISTIC,
        'bearer_signal_strength': gatt.GATT_BEARER_SIGNAL_STRENGTH_CHARACTERISTIC,
        'bearer_signal_strength_reporting_interval': gatt.GATT_BEARER_SIGNAL_STRENGTH_REPORTING_INTERVAL_CHARACTERISTIC,
        'bearer_list_current_calls': gatt.GATT_BEARER_LIST_CURRENT_CALLS_CHARACTERISTIC,
        'content_control_id': gatt.GATT_CONTENT_CONTROL_ID_CHARACTERISTIC,
        'status_flags': gatt.GATT_STATUS_FLAGS_CHARACTERISTIC,
        'incoming_call_target_bearer_uri': gatt.GATT_INCOMING_CALL_TARGET_BEARER_URI_CHARACTERISTIC,
        'call_state': gatt.GATT_CALL_STATE_CHARACTERISTIC,
        'call_control_point': gatt.GATT_CALL_CONTROL_POINT_CHARACTERISTIC,
        'call_control_point_optional_opcodes': gatt.GATT_CALL_CONTROL_POINT_OPTIONAL_OPCODES_CHARACTERISTIC,
        'termination_reason': gatt.GATT_TERMINATION_REASON_CHARACTERISTIC,
        'incoming_call': gatt.GATT_INCOMING_CALL_CHARACTERISTIC,
        'call_friendly_name': gatt.GATT_CALL_FRIENDLY_NAME_CHARACTERISTIC,
    }

    def __init__(self, service_proxy: gatt_client.ServiceProxy) -> None:
        utils.CompositeEventEmitter.__init__(self)
        self.service_proxy = service_proxy

        for field, uuid in self._CHARACTERISTICS.items():
            if characteristics := service_proxy.get_characteristics_by_uuid(uuid):
                setattr(self, field, characteristics[0])

        async def subscribe_async():
            if self.call_state:
                await self.call_state.subscribe(self._on_call_state_change)

        service_proxy.client.connection.abort_on('disconnection', subscribe_async())

    def _on_call_state_change(self, data: bytes) -> None:
        raise NotImplementedError()
