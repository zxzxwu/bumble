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
import asyncio
from dataclasses import dataclass, field
import logging
import struct
from typing import (
    ClassVar,
    Callable,
    Iterable,
    NewType,
    Optional,
    Union,
    Sequence,
    TypeVar,
    TYPE_CHECKING,
)
from typing_extensions import Self

from bumble import core, l2cap, hci
from bumble.colors import color
from bumble.core import (
    InvalidStateError,
    InvalidArgumentError,
    InvalidPacketError,
    ProtocolError,
)

if TYPE_CHECKING:
    from bumble.device import Device, Connection

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=line-too-long

SDP_CONTINUATION_WATCHDOG = 64  # Maximum number of continuations we're willing to do

SDP_PSM = 0x0001

class PduId(hci.SpecableEnum):
    SDP_ERROR_RESPONSE                    = 0x01
    SDP_SERVICE_SEARCH_REQUEST            = 0x02
    SDP_SERVICE_SEARCH_RESPONSE           = 0x03
    SDP_SERVICE_ATTRIBUTE_REQUEST         = 0x04
    SDP_SERVICE_ATTRIBUTE_RESPONSE        = 0x05
    SDP_SERVICE_SEARCH_ATTRIBUTE_REQUEST  = 0x06
    SDP_SERVICE_SEARCH_ATTRIBUTE_RESPONSE = 0x07

class ErrorCode(hci.SpecableEnum):
    INVALID_SDP_VERSION                       = 0x0001
    INVALID_SERVICE_RECORD_HANDLE             = 0x0002
    INVALID_REQUEST_SYNTAX                    = 0x0003
    INVALID_PDU_SIZE                          = 0x0004
    INVALID_CONTINUATION_STATE                = 0x0005
    INSUFFICIENT_RESOURCES_TO_SATISFY_REQUEST = 0x0006

SDP_SERVICE_NAME_ATTRIBUTE_ID_OFFSET        = 0x0000
SDP_SERVICE_DESCRIPTION_ATTRIBUTE_ID_OFFSET = 0x0001
SDP_PROVIDER_NAME_ATTRIBUTE_ID_OFFSET       = 0x0002

SDP_SERVICE_RECORD_HANDLE_ATTRIBUTE_ID               = 0X0000
SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID               = 0X0001
SDP_SERVICE_RECORD_STATE_ATTRIBUTE_ID                = 0X0002
SDP_SERVICE_ID_ATTRIBUTE_ID                          = 0X0003
SDP_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID            = 0X0004
SDP_BROWSE_GROUP_LIST_ATTRIBUTE_ID                   = 0X0005
SDP_LANGUAGE_BASE_ATTRIBUTE_ID_LIST_ATTRIBUTE_ID     = 0X0006
SDP_SERVICE_INFO_TIME_TO_LIVE_ATTRIBUTE_ID           = 0X0007
SDP_SERVICE_AVAILABILITY_ATTRIBUTE_ID                = 0X0008
SDP_BLUETOOTH_PROFILE_DESCRIPTOR_LIST_ATTRIBUTE_ID   = 0X0009
SDP_DOCUMENTATION_URL_ATTRIBUTE_ID                   = 0X000A
SDP_CLIENT_EXECUTABLE_URL_ATTRIBUTE_ID               = 0X000B
SDP_ICON_URL_ATTRIBUTE_ID                            = 0X000C
SDP_ADDITIONAL_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID = 0X000D


# Profile-specific Attribute Identifiers (cf. Assigned Numbers for Service Discovery)
# used by AVRCP, HFP and A2DP
SDP_SUPPORTED_FEATURES_ATTRIBUTE_ID = 0x0311

SDP_ATTRIBUTE_ID_NAMES = {
    SDP_SERVICE_RECORD_HANDLE_ATTRIBUTE_ID:               'SDP_SERVICE_RECORD_HANDLE_ATTRIBUTE_ID',
    SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID:               'SDP_SERVICE_CLASS_ID_LIST_ATTRIBUTE_ID',
    SDP_SERVICE_RECORD_STATE_ATTRIBUTE_ID:                'SDP_SERVICE_RECORD_STATE_ATTRIBUTE_ID',
    SDP_SERVICE_ID_ATTRIBUTE_ID:                          'SDP_SERVICE_ID_ATTRIBUTE_ID',
    SDP_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID:            'SDP_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID',
    SDP_BROWSE_GROUP_LIST_ATTRIBUTE_ID:                   'SDP_BROWSE_GROUP_LIST_ATTRIBUTE_ID',
    SDP_LANGUAGE_BASE_ATTRIBUTE_ID_LIST_ATTRIBUTE_ID:     'SDP_LANGUAGE_BASE_ATTRIBUTE_ID_LIST_ATTRIBUTE_ID',
    SDP_SERVICE_INFO_TIME_TO_LIVE_ATTRIBUTE_ID:           'SDP_SERVICE_INFO_TIME_TO_LIVE_ATTRIBUTE_ID',
    SDP_SERVICE_AVAILABILITY_ATTRIBUTE_ID:                'SDP_SERVICE_AVAILABILITY_ATTRIBUTE_ID',
    SDP_BLUETOOTH_PROFILE_DESCRIPTOR_LIST_ATTRIBUTE_ID:   'SDP_BLUETOOTH_PROFILE_DESCRIPTOR_LIST_ATTRIBUTE_ID',
    SDP_DOCUMENTATION_URL_ATTRIBUTE_ID:                   'SDP_DOCUMENTATION_URL_ATTRIBUTE_ID',
    SDP_CLIENT_EXECUTABLE_URL_ATTRIBUTE_ID:               'SDP_CLIENT_EXECUTABLE_URL_ATTRIBUTE_ID',
    SDP_ICON_URL_ATTRIBUTE_ID:                            'SDP_ICON_URL_ATTRIBUTE_ID',
    SDP_ADDITIONAL_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID: 'SDP_ADDITIONAL_PROTOCOL_DESCRIPTOR_LIST_ATTRIBUTE_ID',
    SDP_SUPPORTED_FEATURES_ATTRIBUTE_ID:                  'SDP_SUPPORTED_FEATURES_ATTRIBUTE_ID',
}

SDP_PUBLIC_BROWSE_ROOT = core.UUID.from_16_bits(0x1002, 'PublicBrowseRoot')

# To be used in searches where an attribute ID list allows a range to be specified
SDP_ALL_ATTRIBUTES_RANGE = (0x0000, 0xFFFF)

# fmt: on
# pylint: enable=line-too-long
# pylint: disable=invalid-name


# -----------------------------------------------------------------------------
class DataElement:
    NIL = 0
    UNSIGNED_INTEGER = 1
    SIGNED_INTEGER = 2
    UUID = 3
    TEXT_STRING = 4
    BOOLEAN = 5
    SEQUENCE = 6
    ALTERNATIVE = 7
    URL = 8

    TYPE_NAMES = {
        NIL: 'NIL',
        UNSIGNED_INTEGER: 'UNSIGNED_INTEGER',
        SIGNED_INTEGER: 'SIGNED_INTEGER',
        UUID: 'UUID',
        TEXT_STRING: 'TEXT_STRING',
        BOOLEAN: 'BOOLEAN',
        SEQUENCE: 'SEQUENCE',
        ALTERNATIVE: 'ALTERNATIVE',
        URL: 'URL',
    }

    type_constructors: dict[int, Callable[..., DataElement]] = {
        NIL: lambda x: DataElement(DataElement.NIL, None),
        UNSIGNED_INTEGER: lambda x, y: DataElement(
            DataElement.UNSIGNED_INTEGER,
            DataElement.unsigned_integer_from_bytes(x),
            value_size=y,
        ),
        SIGNED_INTEGER: lambda x, y: DataElement(
            DataElement.SIGNED_INTEGER,
            DataElement.signed_integer_from_bytes(x),
            value_size=y,
        ),
        UUID: lambda x: DataElement(
            DataElement.UUID, core.UUID.from_bytes(bytes(reversed(x)))
        ),
        TEXT_STRING: lambda x: DataElement(DataElement.TEXT_STRING, x),
        BOOLEAN: lambda x: DataElement(DataElement.BOOLEAN, x[0] == 1),
        SEQUENCE: lambda x: DataElement(
            DataElement.SEQUENCE, DataElement.list_from_bytes(x)
        ),
        ALTERNATIVE: lambda x: DataElement(
            DataElement.ALTERNATIVE, DataElement.list_from_bytes(x)
        ),
        URL: lambda x: DataElement(DataElement.URL, x.decode('utf8')),
    }

    def __init__(self, element_type, value, value_size=None):
        self.type = element_type
        self.value = value
        self.value_size = value_size
        # Used as a cache when parsing from bytes so we can emit a byte-for-byte replica
        self.bytes = None
        if element_type in (DataElement.UNSIGNED_INTEGER, DataElement.SIGNED_INTEGER):
            if value_size is None:
                raise InvalidArgumentError(
                    'integer types must have a value size specified'
                )

    @staticmethod
    def nil() -> DataElement:
        return DataElement(DataElement.NIL, None)

    @staticmethod
    def unsigned_integer(value: int, value_size: int) -> DataElement:
        return DataElement(DataElement.UNSIGNED_INTEGER, value, value_size)

    @staticmethod
    def unsigned_integer_8(value: int) -> DataElement:
        return DataElement(DataElement.UNSIGNED_INTEGER, value, value_size=1)

    @staticmethod
    def unsigned_integer_16(value: int) -> DataElement:
        return DataElement(DataElement.UNSIGNED_INTEGER, value, value_size=2)

    @staticmethod
    def unsigned_integer_32(value: int) -> DataElement:
        return DataElement(DataElement.UNSIGNED_INTEGER, value, value_size=4)

    @staticmethod
    def signed_integer(value: int, value_size: int) -> DataElement:
        return DataElement(DataElement.SIGNED_INTEGER, value, value_size)

    @staticmethod
    def signed_integer_8(value: int) -> DataElement:
        return DataElement(DataElement.SIGNED_INTEGER, value, value_size=1)

    @staticmethod
    def signed_integer_16(value: int) -> DataElement:
        return DataElement(DataElement.SIGNED_INTEGER, value, value_size=2)

    @staticmethod
    def signed_integer_32(value: int) -> DataElement:
        return DataElement(DataElement.SIGNED_INTEGER, value, value_size=4)

    @staticmethod
    def uuid(value: core.UUID) -> DataElement:
        return DataElement(DataElement.UUID, value)

    @staticmethod
    def text_string(value: bytes) -> DataElement:
        return DataElement(DataElement.TEXT_STRING, value)

    @staticmethod
    def boolean(value: bool) -> DataElement:
        return DataElement(DataElement.BOOLEAN, value)

    @staticmethod
    def sequence(value: Iterable[DataElement]) -> DataElement:
        return DataElement(DataElement.SEQUENCE, value)

    @staticmethod
    def alternative(value: Iterable[DataElement]) -> DataElement:
        return DataElement(DataElement.ALTERNATIVE, value)

    @staticmethod
    def url(value: str) -> DataElement:
        return DataElement(DataElement.URL, value)

    @staticmethod
    def unsigned_integer_from_bytes(data):
        if len(data) == 1:
            return data[0]

        if len(data) == 2:
            return struct.unpack('>H', data)[0]

        if len(data) == 4:
            return struct.unpack('>I', data)[0]

        if len(data) == 8:
            return struct.unpack('>Q', data)[0]

        raise InvalidPacketError(f'invalid integer length {len(data)}')

    @staticmethod
    def signed_integer_from_bytes(data):
        if len(data) == 1:
            return struct.unpack('b', data)[0]

        if len(data) == 2:
            return struct.unpack('>h', data)[0]

        if len(data) == 4:
            return struct.unpack('>i', data)[0]

        if len(data) == 8:
            return struct.unpack('>q', data)[0]

        raise InvalidPacketError(f'invalid integer length {len(data)}')

    @classmethod
    def list_from_bytes(cls, data: bytes) -> list[DataElement]:
        elements = []
        while data:
            element = DataElement.from_bytes(data)
            elements.append(element)
            data = data[len(bytes(element)) :]
        return elements

    @classmethod
    def parse_from_bytes(cls, data: bytes, offset: int) -> tuple[int, DataElement]:
        element = DataElement.from_bytes(data[offset:])
        return offset + len(bytes(element)), element

    @classmethod
    def from_bytes(cls, data: bytes) -> DataElement:
        element_type = data[0] >> 3
        size_index = data[0] & 7
        value_offset = 0
        if size_index == 0:
            if element_type == DataElement.NIL:
                value_size = 0
            else:
                value_size = 1
        elif size_index == 1:
            value_size = 2
        elif size_index == 2:
            value_size = 4
        elif size_index == 3:
            value_size = 8
        elif size_index == 4:
            value_size = 16
        elif size_index == 5:
            value_size = data[1]
            value_offset = 1
        elif size_index == 6:
            value_size = struct.unpack('>H', data[1:3])[0]
            value_offset = 2
        else:  # size_index == 7
            value_size = struct.unpack('>I', data[1:5])[0]
            value_offset = 4

        value_data = data[1 + value_offset : 1 + value_offset + value_size]
        constructor = DataElement.type_constructors.get(element_type)
        if constructor:
            if element_type in (
                DataElement.UNSIGNED_INTEGER,
                DataElement.SIGNED_INTEGER,
            ):
                result = constructor(value_data, value_size)
            else:
                result = constructor(value_data)
        else:
            result = DataElement(element_type, value_data)
        result.bytes = data[
            : 1 + value_offset + value_size
        ]  # Keep a copy so we can re-serialize to an exact replica
        return result

    def __bytes__(self) -> bytes:
        # Return early if we have a cache
        if self.bytes:
            return self.bytes

        if self.type == DataElement.NIL:
            data = b''
        elif self.type == DataElement.UNSIGNED_INTEGER:
            if self.value < 0:
                raise InvalidArgumentError('UNSIGNED_INTEGER cannot be negative')

            if self.value_size == 1:
                data = struct.pack('B', self.value)
            elif self.value_size == 2:
                data = struct.pack('>H', self.value)
            elif self.value_size == 4:
                data = struct.pack('>I', self.value)
            elif self.value_size == 8:
                data = struct.pack('>Q', self.value)
            else:
                raise InvalidArgumentError('invalid value_size')
        elif self.type == DataElement.SIGNED_INTEGER:
            if self.value_size == 1:
                data = struct.pack('b', self.value)
            elif self.value_size == 2:
                data = struct.pack('>h', self.value)
            elif self.value_size == 4:
                data = struct.pack('>i', self.value)
            elif self.value_size == 8:
                data = struct.pack('>q', self.value)
            else:
                raise InvalidArgumentError('invalid value_size')
        elif self.type == DataElement.UUID:
            data = bytes(reversed(bytes(self.value)))
        elif self.type == DataElement.URL:
            data = self.value.encode('utf8')
        elif self.type == DataElement.BOOLEAN:
            data = bytes([1 if self.value else 0])
        elif self.type in (DataElement.SEQUENCE, DataElement.ALTERNATIVE):
            data = b''.join([bytes(element) for element in self.value])
        else:
            data = self.value

        size = len(data)
        size_bytes = b''
        if self.type == DataElement.NIL:
            if size != 0:
                raise InvalidArgumentError('NIL must be empty')
            size_index = 0
        elif self.type in (
            DataElement.UNSIGNED_INTEGER,
            DataElement.SIGNED_INTEGER,
            DataElement.UUID,
        ):
            if size <= 1:
                size_index = 0
            elif size == 2:
                size_index = 1
            elif size == 4:
                size_index = 2
            elif size == 8:
                size_index = 3
            elif size == 16:
                size_index = 4
            else:
                raise InvalidArgumentError('invalid data size')
        elif self.type in (
            DataElement.TEXT_STRING,
            DataElement.SEQUENCE,
            DataElement.ALTERNATIVE,
            DataElement.URL,
        ):
            if size <= 0xFF:
                size_index = 5
                size_bytes = bytes([size])
            elif size <= 0xFFFF:
                size_index = 6
                size_bytes = struct.pack('>H', size)
            elif size <= 0xFFFFFFFF:
                size_index = 7
                size_bytes = struct.pack('>I', size)
            else:
                raise InvalidArgumentError('invalid data size')
        elif self.type == DataElement.BOOLEAN:
            if size != 1:
                raise InvalidArgumentError('boolean must be 1 byte')
            size_index = 0
        else:
            raise RuntimeError("internal error - self.type not supported")

        self.bytes = bytes([self.type << 3 | size_index]) + size_bytes + data
        return self.bytes

    def to_string(self, pretty: bool = False, indentation: int = 0) -> str:
        prefix = '  ' * indentation
        type_name = hci.name_or_number(self.TYPE_NAMES, self.type)
        if self.type == DataElement.NIL:
            value_string = ''
        elif self.type in (DataElement.SEQUENCE, DataElement.ALTERNATIVE):
            container_separator = '\n' if pretty else ''
            element_separator = '\n' if pretty else ','
            elements = [
                element.to_string(pretty, indentation + 1 if pretty else 0)
                for element in self.value
            ]
            value_string = (
                f'[{container_separator}'
                f'{element_separator.join(elements)}'
                f'{container_separator}{prefix}]'
            )
        elif self.type in (DataElement.UNSIGNED_INTEGER, DataElement.SIGNED_INTEGER):
            value_string = f'{self.value}#{self.value_size}'
        elif isinstance(self.value, DataElement):
            value_string = self.value.to_string(pretty, indentation)
        else:
            value_string = str(self.value)
        return f'{prefix}{type_name}({value_string})'

    def __str__(self) -> str:
        return self.to_string()


# -----------------------------------------------------------------------------
class ServiceAttribute:
    def __init__(self, attribute_id: int, value: DataElement) -> None:
        self.id = attribute_id
        self.value = value

    @staticmethod
    def list_from_data_elements(
        elements: Sequence[DataElement],
    ) -> list[ServiceAttribute]:
        attribute_list = []
        for i in range(0, len(elements) // 2):
            attribute_id, attribute_value = elements[2 * i : 2 * (i + 1)]
            if attribute_id.type != DataElement.UNSIGNED_INTEGER:
                logger.warning('attribute ID element is not an integer')
                continue
            attribute_list.append(ServiceAttribute(attribute_id.value, attribute_value))

        return attribute_list

    @staticmethod
    def find_attribute_in_list(
        attribute_list: Iterable[ServiceAttribute], attribute_id: int
    ) -> Optional[DataElement]:
        return next(
            (
                attribute.value
                for attribute in attribute_list
                if attribute.id == attribute_id
            ),
            None,
        )

    @staticmethod
    def id_name(id_code: int) -> str:
        return hci.name_or_number(SDP_ATTRIBUTE_ID_NAMES, id_code)

    @staticmethod
    def is_uuid_in_value(uuid: core.UUID, value: DataElement) -> bool:
        # Find if a uuid matches a value, either directly or recursing into sequences
        if value.type == DataElement.UUID:
            return value.value == uuid

        if value.type == DataElement.SEQUENCE:
            for element in value.value:
                if ServiceAttribute.is_uuid_in_value(uuid, element):
                    return True
            return False

        return False

    def to_string(self, with_colors: bool = False) -> str:
        if with_colors:
            return (
                f'Attribute(id={color(self.id_name(self.id),"magenta")},'
                f'value={self.value})'
            )

        return f'Attribute(id={self.id_name(self.id)},value={self.value})'

    def __str__(self) -> str:
        return self.to_string()


# -----------------------------------------------------------------------------
@dataclass
class SDP_PDU:
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.2 PROTOCOL DATA UNIT FORMAT
    '''

    RESPONSE_PDU_IDS = {
        PduId.SDP_SERVICE_SEARCH_REQUEST: PduId.SDP_SERVICE_SEARCH_RESPONSE,
        PduId.SDP_SERVICE_ATTRIBUTE_REQUEST: PduId.SDP_SERVICE_ATTRIBUTE_RESPONSE,
        PduId.SDP_SERVICE_SEARCH_ATTRIBUTE_REQUEST: PduId.SDP_SERVICE_SEARCH_ATTRIBUTE_RESPONSE,
    }

    subclasses: ClassVar[dict[int, type[SDP_PDU]]] = {}
    fields: ClassVar[hci.Fields]
    pdu_id: int = field(default=0, init=False)
    name: str = field(default='', init=False)
    _payload: Optional[bytes] = field(default=None, init=False)

    transaction_id: int

    @classmethod
    def from_bytes(cls, pdu: bytes) -> SDP_PDU:
        pdu_id, transaction_id, _parameters_length = struct.unpack_from('>BHH', pdu, 0)

        subclass = cls.subclasses.get(pdu_id)
        if subclass is None:
            instance = cls(transaction_id=transaction_id)
            instance.name = PduId(pdu_id).name
            instance.pdu_id = pdu_id
        else:
            instance = subclass(
                transaction_id=transaction_id,
                **hci.HCI_Object.dict_from_bytes(pdu, offset=5, fields=subclass.fields),
            )
        instance.payload = pdu[5:]
        return instance

    @classmethod
    def parse_service_record_handle_list_preceded_by_count(
        cls, data: bytes, offset: int
    ) -> tuple[int, list[int]]:
        count = struct.unpack_from('>H', data, offset - 2)[0]
        handle_list = [
            struct.unpack_from('>I', data, offset + x * 4)[0] for x in range(count)
        ]
        return offset + count * 4, handle_list

    @classmethod
    def parse_bytes_preceded_by_length(
        cls, data: bytes, offset: int
    ) -> tuple[int, bytes]:
        length = struct.unpack_from('>H', data, offset - 2)[0]
        return offset + length, data[offset : offset + length]

    _PDU = TypeVar('_PDU', bound='SDP_PDU')

    @classmethod
    def subclass(cls, subclass: type[_PDU]) -> type[_PDU]:
        # Register a factory for this class
        cls.subclasses[subclass.pdu_id] = subclass
        subclass.fields = hci.HCI_Object.fields_from_dataclass(subclass)
        return subclass

    @property
    def payload(self) -> bytes:
        if self._payload is None:
            parameters = hci.HCI_Object.dict_to_bytes(self.__dict__, self.fields)
            self._payload = (
                struct.pack('>BHH', self.pdu_id, self.transaction_id, len(parameters))
                + parameters
            )
        return self._payload

    @payload.setter
    def payload(self, value: bytes):
        self._payload = value

    def __bytes__(self) -> bytes:
        return self.payload

    def __str__(self) -> str:
        result = f'{color(self.name, "blue")} [TID={self.transaction_id}]'
        if fields := getattr(self, 'fields', None):
            result += ':\n' + hci.HCI_Object.format_fields(self.__dict__, fields, '  ')
        elif len(self.payload) > 1:
            result += f': {self.payload.hex()}'
        return result


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ErrorResponse(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.4.1 SDP_ErrorResponse PDU
    '''

    pdu_id = PduId.SDP_ERROR_RESPONSE
    name = 'SDP_ERROR_RESPONSE'

    error_code: int = field(metadata=ErrorCode.type_metadata(2))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceSearchRequest(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.5.1 SDP_ServiceSearchRequest PDU
    '''

    pdu_id = PduId.SDP_SERVICE_SEARCH_REQUEST
    name = 'SDP_SERVICE_SEARCH_REQUEST'

    service_search_pattern: DataElement = field(
        metadata=hci.metadata(DataElement.parse_from_bytes)
    )
    maximum_service_record_count: int = field(metadata=hci.metadata('>2'))
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceSearchResponse(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.5.2 SDP_ServiceSearchResponse PDU
    '''

    pdu_id = PduId.SDP_SERVICE_SEARCH_RESPONSE
    name = 'SDP_SERVICE_SEARCH_RESPONSE'

    total_service_record_count: int = field(metadata=hci.metadata('>2'))
    current_service_record_count: int = field(metadata=hci.metadata('>2'))
    service_record_handle_list: Sequence[int] = field(
        metadata=hci.metadata(
            SDP_PDU.parse_service_record_handle_list_preceded_by_count
        )
    )
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceAttributeRequest(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.6.1 SDP_ServiceAttributeRequest PDU
    '''

    pdu_id = PduId.SDP_SERVICE_ATTRIBUTE_REQUEST
    name = 'SDP_SERVICE_ATTRIBUTE_REQUEST'

    service_record_handle: int = field(metadata=hci.metadata('>4'))
    maximum_attribute_byte_count: int = field(metadata=hci.metadata('>2'))
    attribute_id_list: DataElement = field(
        metadata=hci.metadata(DataElement.parse_from_bytes)
    )
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceAttributeResponse(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.6.2 SDP_ServiceAttributeResponse PDU
    '''

    pdu_id = PduId.SDP_SERVICE_ATTRIBUTE_RESPONSE
    name = 'SDP_SERVICE_ATTRIBUTE_RESPONSE'

    attribute_list_byte_count: int = field(metadata=hci.metadata('>2'))
    attribute_list: bytes = field(
        metadata=hci.metadata(SDP_PDU.parse_bytes_preceded_by_length)
    )
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceSearchAttributeRequest(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.7.1 SDP_ServiceSearchAttributeRequest PDU
    '''

    pdu_id = PduId.SDP_SERVICE_SEARCH_ATTRIBUTE_REQUEST
    name = 'SDP_SERVICE_SEARCH_ATTRIBUTE_REQUEST'

    service_search_pattern: DataElement = field(
        metadata=hci.metadata(DataElement.parse_from_bytes)
    )
    maximum_attribute_byte_count: int = field(metadata=hci.metadata('>2'))
    attribute_id_list: DataElement = field(
        metadata=hci.metadata(DataElement.parse_from_bytes)
    )
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
@SDP_PDU.subclass
@dataclass
class SDP_ServiceSearchAttributeResponse(SDP_PDU):
    '''
    See Bluetooth spec @ Vol 3, Part B - 4.7.2 SDP_ServiceSearchAttributeResponse PDU
    '''

    pdu_id = PduId.SDP_SERVICE_SEARCH_ATTRIBUTE_RESPONSE
    name = 'SDP_SERVICE_SEARCH_ATTRIBUTE_RESPONSE'

    attribute_lists_byte_count: int = field(metadata=hci.metadata('>2'))
    attribute_lists: bytes = field(
        metadata=hci.metadata(SDP_PDU.parse_bytes_preceded_by_length)
    )
    continuation_state: bytes = field(metadata=hci.metadata('*'))


# -----------------------------------------------------------------------------
class Client:
    def __init__(self, connection: Connection, mtu: int = 0) -> None:
        self.connection = connection
        self.channel: Optional[l2cap.ClassicChannel] = None
        self.mtu = mtu
        self.request_semaphore = asyncio.Semaphore(1)
        self.pending_request: Optional[SDP_PDU] = None
        self.pending_response: Optional[asyncio.futures.Future[SDP_PDU]] = None
        self.next_transaction_id = 0

    async def connect(self) -> None:
        self.channel = await self.connection.create_l2cap_channel(
            spec=(
                l2cap.ClassicChannelSpec(SDP_PSM, self.mtu)
                if self.mtu
                else l2cap.ClassicChannelSpec(SDP_PSM)
            )
        )
        self.channel.sink = self.on_pdu

    async def disconnect(self) -> None:
        if self.channel:
            await self.channel.disconnect()
            self.channel = None

    def make_transaction_id(self) -> int:
        transaction_id = self.next_transaction_id
        self.next_transaction_id = (self.next_transaction_id + 1) & 0xFFFF
        return transaction_id

    def on_pdu(self, pdu: bytes) -> None:
        if not self.pending_request:
            logger.warning('received response with no pending request')
            return
        assert self.pending_response is not None

        response = SDP_PDU.from_bytes(pdu)

        # Check that the transaction ID is what we expect
        if self.pending_request.transaction_id != response.transaction_id:
            logger.warning(
                f"received response with transaction ID {response.transaction_id} "
                f"but expected {self.pending_request.transaction_id}"
            )
            return

        # Check if the response is an error
        if isinstance(response, SDP_ErrorResponse):
            self.pending_response.set_exception(
                ProtocolError(error_code=response.error_code)
            )
            return

        # Check that the type of the response matches the request
        if response.pdu_id != SDP_PDU.RESPONSE_PDU_IDS.get(self.pending_request.pdu_id):
            logger.warning("response type mismatch")
            return

        self.pending_response.set_result(response)

    async def send_request(self, request: SDP_PDU) -> SDP_PDU:
        assert self.channel is not None
        async with self.request_semaphore:
            assert self.pending_request is None
            assert self.pending_response is None

            # Create a future value to hold the eventual response
            self.pending_response = asyncio.get_running_loop().create_future()
            self.pending_request = request

            try:
                self.channel.send_pdu(bytes(request))
                return await self.pending_response
            finally:
                self.pending_request = None
                self.pending_response = None

    async def search_services(self, uuids: Iterable[core.UUID]) -> list[int]:
        """
        Search for services by UUID.

        Args:
          uuids: service the UUIDs to search for.

        Returns:
          A list of matching service record handles.
        """
        if self.pending_request is not None:
            raise InvalidStateError('request already pending')
        if self.channel is None:
            raise InvalidStateError('L2CAP not connected')

        service_search_pattern = DataElement.sequence(
            [DataElement.uuid(uuid) for uuid in uuids]
        )

        # Request and accumulate until there's no more continuation
        service_record_handle_list: list[int] = []
        continuation_state = bytes([0])
        watchdog = SDP_CONTINUATION_WATCHDOG
        while watchdog > 0:
            response = await self.send_request(
                SDP_ServiceSearchRequest(
                    transaction_id=self.make_transaction_id(),
                    service_search_pattern=service_search_pattern,
                    maximum_service_record_count=0xFFFF,
                    continuation_state=continuation_state,
                )
            )
            logger.debug(f'<<< Response: {response}')
            assert isinstance(response, SDP_ServiceSearchResponse)
            service_record_handle_list += response.service_record_handle_list
            continuation_state = response.continuation_state
            if len(continuation_state) == 1 and continuation_state[0] == 0:
                break
            logger.debug(f'continuation: {continuation_state.hex()}')
            watchdog -= 1

        return service_record_handle_list

    async def search_attributes(
        self,
        uuids: Iterable[core.UUID],
        attribute_ids: Iterable[Union[int, tuple[int, int]]],
    ) -> list[list[ServiceAttribute]]:
        """
        Search for attributes by UUID and attribute IDs.

        Args:
          uuids: the service UUIDs to search for.
          attribute_ids: list of attribute IDs or (start, end) attribute ID ranges.
          (use (0, 0xFFFF) to include all attributes)

        Returns:
          A list of list of attributes, one list per matching service.
        """
        if self.pending_request is not None:
            raise InvalidStateError('request already pending')
        if self.channel is None:
            raise InvalidStateError('L2CAP not connected')

        service_search_pattern = DataElement.sequence(
            [DataElement.uuid(uuid) for uuid in uuids]
        )
        attribute_id_list = DataElement.sequence(
            [
                (
                    DataElement.unsigned_integer_32(
                        attribute_id[0] << 16 | attribute_id[1]
                    )
                    if isinstance(attribute_id, tuple)
                    else DataElement.unsigned_integer_16(attribute_id)
                )
                for attribute_id in attribute_ids
            ]
        )

        # Request and accumulate until there's no more continuation
        accumulator = b''
        continuation_state = bytes([0])
        watchdog = SDP_CONTINUATION_WATCHDOG
        while watchdog > 0:
            response = await self.send_request(
                SDP_ServiceSearchAttributeRequest(
                    transaction_id=self.make_transaction_id(),
                    service_search_pattern=service_search_pattern,
                    maximum_attribute_byte_count=0xFFFF,
                    attribute_id_list=attribute_id_list,
                    continuation_state=continuation_state,
                )
            )
            logger.debug(f'<<< Response: {response}')
            assert isinstance(response, SDP_ServiceSearchAttributeResponse)
            accumulator += response.attribute_lists
            continuation_state = response.continuation_state
            if len(continuation_state) == 1 and continuation_state[0] == 0:
                break
            logger.debug(f'continuation: {continuation_state.hex()}')
            watchdog -= 1

        # Parse the result into attribute lists
        attribute_lists_sequences = DataElement.from_bytes(accumulator)
        if attribute_lists_sequences.type != DataElement.SEQUENCE:
            logger.warning('unexpected data type')
            return []

        return [
            ServiceAttribute.list_from_data_elements(sequence.value)
            for sequence in attribute_lists_sequences.value
            if sequence.type == DataElement.SEQUENCE
        ]

    async def get_attributes(
        self,
        service_record_handle: int,
        attribute_ids: Iterable[Union[int, tuple[int, int]]],
    ) -> list[ServiceAttribute]:
        """
        Get attributes for a service.

        Args:
          service_record_handle: the handle for a service
          attribute_ids: list or attribute IDs or (start, end) attribute ID handles.

        Returns:
          A list of attributes.
        """
        if self.pending_request is not None:
            raise InvalidStateError('request already pending')
        if self.channel is None:
            raise InvalidStateError('L2CAP not connected')

        attribute_id_list = DataElement.sequence(
            [
                (
                    DataElement.unsigned_integer_32(
                        attribute_id[0] << 16 | attribute_id[1]
                    )
                    if isinstance(attribute_id, tuple)
                    else DataElement.unsigned_integer_16(attribute_id)
                )
                for attribute_id in attribute_ids
            ]
        )

        # Request and accumulate until there's no more continuation
        accumulator = b''
        continuation_state = bytes([0])
        watchdog = SDP_CONTINUATION_WATCHDOG
        while watchdog > 0:
            response = await self.send_request(
                SDP_ServiceAttributeRequest(
                    transaction_id=self.make_transaction_id(),
                    service_record_handle=service_record_handle,
                    maximum_attribute_byte_count=0xFFFF,
                    attribute_id_list=attribute_id_list,
                    continuation_state=continuation_state,
                )
            )
            logger.debug(f'<<< Response: {response}')
            assert isinstance(response, SDP_ServiceAttributeResponse)
            accumulator += response.attribute_list
            continuation_state = response.continuation_state
            if len(continuation_state) == 1 and continuation_state[0] == 0:
                break
            logger.debug(f'continuation: {continuation_state.hex()}')
            watchdog -= 1

        # Parse the result into a list of attributes
        attribute_list_sequence = DataElement.from_bytes(accumulator)
        if attribute_list_sequence.type != DataElement.SEQUENCE:
            logger.warning('unexpected data type')
            return []

        return ServiceAttribute.list_from_data_elements(attribute_list_sequence.value)

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()


# -----------------------------------------------------------------------------
class Server:
    CONTINUATION_STATE = bytes([0x01, 0x00])
    channel: Optional[l2cap.ClassicChannel]
    Service = NewType('Service', list[ServiceAttribute])
    service_records: dict[int, Service]
    current_response: Union[None, bytes, tuple[int, list[int]]]

    def __init__(self, device: Device) -> None:
        self.device = device
        self.service_records = {}  # Service records maps, by record handle
        self.channel = None
        self.current_response = None  # Current response data, used for continuations

    def register(self, l2cap_channel_manager: l2cap.ChannelManager) -> None:
        l2cap_channel_manager.create_classic_server(
            spec=l2cap.ClassicChannelSpec(psm=SDP_PSM), handler=self.on_connection
        )

    def send_response(self, response):
        logger.debug(f'{color(">>> Sending SDP Response", "blue")}: {response}')
        self.channel.send_pdu(response)

    def match_services(self, search_pattern: DataElement) -> dict[int, Service]:
        # Find the services for which the attributes in the pattern is a subset of the
        # service's attribute values (NOTE: the value search recurses into sequences)
        matching_services = {}
        for handle, service in self.service_records.items():
            for uuid in search_pattern.value:
                found = False
                for attribute in service:
                    if ServiceAttribute.is_uuid_in_value(uuid.value, attribute.value):
                        found = True
                        break
                if found:
                    matching_services[handle] = service
                    break

        return matching_services

    def on_connection(self, channel: l2cap.ClassicChannel) -> None:
        self.channel = channel
        self.channel.sink = self.on_pdu

    def on_pdu(self, pdu: bytes) -> None:
        try:
            sdp_pdu = SDP_PDU.from_bytes(pdu)
        except Exception as error:
            logger.warning(color(f'failed to parse SDP Request PDU: {error}', 'red'))
            self.send_response(
                SDP_ErrorResponse(
                    transaction_id=0, error_code=ErrorCode.INVALID_REQUEST_SYNTAX
                )
            )

        logger.debug(f'{color("<<< Received SDP Request", "green")}: {sdp_pdu}')

        # Find the handler method
        handler_name = f'on_{sdp_pdu.name.lower()}'
        handler = getattr(self, handler_name, None)
        if handler:
            try:
                handler(sdp_pdu)
            except Exception as error:
                logger.exception(f'{color("!!! Exception in handler:", "red")} {error}')
                self.send_response(
                    SDP_ErrorResponse(
                        transaction_id=sdp_pdu.transaction_id,
                        error_code=ErrorCode.INSUFFICIENT_RESOURCES_TO_SATISFY_REQUEST,
                    )
                )
        else:
            logger.error(color('SDP Request not handled???', 'red'))
            self.send_response(
                SDP_ErrorResponse(
                    transaction_id=sdp_pdu.transaction_id,
                    error_code=ErrorCode.INVALID_REQUEST_SYNTAX,
                )
            )

    def check_continuation(
        self,
        continuation_state: bytes,
        transaction_id: int,
    ) -> Optional[bool]:
        # Check if this is a valid continuation
        if len(continuation_state) > 1:
            if (
                self.current_response is None
                or continuation_state != self.CONTINUATION_STATE
            ):
                self.send_response(
                    SDP_ErrorResponse(
                        transaction_id=transaction_id,
                        error_code=ErrorCode.INVALID_CONTINUATION_STATE,
                    )
                )
                return None
            return True

        # Cleanup any partial response leftover
        self.current_response = None

        return False

    def get_next_response_payload(self, maximum_size):
        if len(self.current_response) > maximum_size:
            payload = self.current_response[:maximum_size]
            continuation_state = Server.CONTINUATION_STATE
            self.current_response = self.current_response[maximum_size:]
        else:
            payload = self.current_response
            continuation_state = bytes([0])
            self.current_response = None

        return (payload, continuation_state)

    @staticmethod
    def get_service_attributes(
        service: Service, attribute_ids: Iterable[DataElement]
    ) -> DataElement:
        attributes = []
        for attribute_id in attribute_ids:
            if attribute_id.value_size == 4:
                # Attribute ID range
                id_range_start = attribute_id.value >> 16
                id_range_end = attribute_id.value & 0xFFFF
            else:
                id_range_start = attribute_id.value
                id_range_end = attribute_id.value
            attributes += [
                attribute
                for attribute in service
                if attribute.id >= id_range_start and attribute.id <= id_range_end
            ]

        # Return the matching attributes, sorted by attribute id
        attributes.sort(key=lambda x: x.id)
        attribute_list = DataElement.sequence([])
        for attribute in attributes:
            attribute_list.value.append(DataElement.unsigned_integer_16(attribute.id))
            attribute_list.value.append(attribute.value)

        return attribute_list

    def on_sdp_service_search_request(self, request: SDP_ServiceSearchRequest) -> None:
        # Check if this is a continuation
        if (
            continuation := self.check_continuation(
                request.continuation_state, request.transaction_id
            )
        ) is None:
            return

        if not continuation:
            # Find the matching services
            matching_services = self.match_services(request.service_search_pattern)
            service_record_handles = list(matching_services.keys())
            logger.debug(f'Service Record Handles: {service_record_handles}')

            # Only return up to the maximum requested
            service_record_handles_subset = service_record_handles[
                : request.maximum_service_record_count
            ]

            self.current_response = (
                len(service_record_handles),
                service_record_handles_subset,
            )

        # Respond, keeping any unsent handles for later
        assert isinstance(self.current_response, tuple)
        assert self.channel is not None
        total_service_record_count, service_record_handles = self.current_response
        maximum_service_record_count = (self.channel.peer_mtu - 11) // 4
        service_record_handles_remaining = service_record_handles[
            maximum_service_record_count:
        ]
        service_record_handles = service_record_handles[:maximum_service_record_count]
        self.current_response = (
            total_service_record_count,
            service_record_handles_remaining,
        )
        continuation_state = (
            Server.CONTINUATION_STATE
            if service_record_handles_remaining
            else bytes([0])
        )
        service_record_handle_list = b''.join(
            [struct.pack('>I', handle) for handle in service_record_handles]
        )
        self.send_response(
            SDP_ServiceSearchResponse(
                transaction_id=request.transaction_id,
                total_service_record_count=total_service_record_count,
                current_service_record_count=len(service_record_handles),
                service_record_handle_list=service_record_handle_list,
                continuation_state=continuation_state,
            )
        )

    def on_sdp_service_attribute_request(
        self, request: SDP_ServiceAttributeRequest
    ) -> None:
        # Check if this is a continuation
        if (
            continuation := self.check_continuation(
                request.continuation_state, request.transaction_id
            )
        ) is None:
            return

        if not continuation:
            # Check that the service exists
            service = self.service_records.get(request.service_record_handle)
            if service is None:
                self.send_response(
                    SDP_ErrorResponse(
                        transaction_id=request.transaction_id,
                        error_code=ErrorCode.INVALID_SERVICE_RECORD_HANDLE,
                    )
                )
                return

            # Get the attributes for the service
            attribute_list = Server.get_service_attributes(
                service, request.attribute_id_list.value
            )

            # Serialize to a byte array
            logger.debug(f'Attributes: {attribute_list}')
            self.current_response = bytes(attribute_list)

        # Respond, keeping any pending chunks for later
        assert self.channel is not None
        maximum_attribute_byte_count = min(
            request.maximum_attribute_byte_count, self.channel.peer_mtu - 9
        )
        attribute_list_response, continuation_state = self.get_next_response_payload(
            maximum_attribute_byte_count
        )
        self.send_response(
            SDP_ServiceAttributeResponse(
                transaction_id=request.transaction_id,
                attribute_list_byte_count=len(attribute_list_response),
                attribute_list=attribute_list_response,
                continuation_state=continuation_state,
            )
        )

    def on_sdp_service_search_attribute_request(
        self, request: SDP_ServiceSearchAttributeRequest
    ) -> None:
        # Check if this is a continuation
        if (
            continuation := self.check_continuation(
                request.continuation_state, request.transaction_id
            )
        ) is None:
            return

        if not continuation:
            # Find the matching services
            matching_services = self.match_services(
                request.service_search_pattern
            ).values()

            # Filter the required attributes
            attribute_lists = DataElement.sequence([])
            for service in matching_services:
                attribute_list = Server.get_service_attributes(
                    service, request.attribute_id_list.value
                )
                if attribute_list.value:
                    attribute_lists.value.append(attribute_list)

            # Serialize to a byte array
            logger.debug(f'Search response: {attribute_lists}')
            self.current_response = bytes(attribute_lists)

        # Respond, keeping any pending chunks for later
        assert self.channel is not None
        maximum_attribute_byte_count = min(
            request.maximum_attribute_byte_count, self.channel.peer_mtu - 9
        )
        attribute_lists_response, continuation_state = self.get_next_response_payload(
            maximum_attribute_byte_count
        )
        self.send_response(
            SDP_ServiceSearchAttributeResponse(
                transaction_id=request.transaction_id,
                attribute_lists_byte_count=len(attribute_lists_response),
                attribute_lists=attribute_lists_response,
                continuation_state=continuation_state,
            )
        )
