# Copyright 2021-2025 Google LLC
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

from dataclasses import dataclass

from bumble import hci

# TODO: Implement Advertising Physical Channel PDUs.


class Opcode(hci.SpecableEnum):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2. LL Control PDU.
    '''

    # fmt: off
    LL_CONNECTION_UPDATE_IND   = 0x00
    LL_CHANNEL_MAP_IND         = 0x01
    LL_TERMINATE_IND           = 0x02
    LL_ENC_REQ                 = 0x03
    LL_ENC_RSP                 = 0x04
    LL_START_ENC_REQ           = 0x05
    LL_START_ENC_RSP           = 0x06
    LL_UNKNOWN_RSP             = 0x07
    LL_FEATURE_REQ             = 0x08
    LL_FEATURE_RSP             = 0x09
    LL_PAUSE_ENC_REQ           = 0x0A
    LL_PAUSE_ENC_RSP           = 0x0B
    LL_VERSION_IND             = 0x0C
    LL_REJECT_IND              = 0x0D
    LL_PERIPHERAL_FEATURE_REQ  = 0x0E
    LL_CONNECTION_PARAM_REQ    = 0x0F
    LL_CONNECTION_PARAM_RSP    = 0x10
    LL_REJECT_EXT_IND          = 0x11
    LL_PING_REQ                = 0x12
    LL_PING_RSP                = 0x13
    LL_LENGTH_REQ              = 0x14
    LL_LENGTH_RSP              = 0x15
    LL_PHY_REQ                 = 0x16
    LL_PHY_RSP                 = 0x17
    LL_PHY_UPDATE_IND          = 0x18
    LL_MIN_USED_CHANNELS_IND   = 0x19
    LL_CTE_REQ                 = 0x1A
    LL_CTE_RSP                 = 0x1B
    LL_PERIODIC_SYNC_IND       = 0x1C
    LL_CLOCK_ACCURACY_REQ      = 0x1D
    LL_CLOCK_ACCURACY_RSP      = 0x1E
    LL_CIS_REQ                 = 0x1F
    LL_CIS_RSP                 = 0x20
    LL_CIS_IND                 = 0x21
    LL_CIS_TERMINATE_IND       = 0x22
    LL_POWER_CONTROL_REQ       = 0x23
    LL_POWER_CONTROL_RSP       = 0x24
    LL_POWER_CHANGE_IND        = 0x25
    LL_SUBRATE_REQ             = 0x26
    LL_SUBRATE_IND             = 0x27
    LL_CHANNEL_REPORTING_IND   = 0x28
    LL_CHANNEL_STATUS_IND      = 0x29
    LL_PERIODIC_SYNC_WR_IND    = 0x2A
    LL_FEATURE_EXT_REQ         = 0x2B 
    LL_FEATURE_EXT_RSP         = 0x2C 
    LL_CS_SEC_RSP              = 0x2D 
    LL_CS_CAPABILITIES_REQ     = 0x2E 
    LL_CS_CAPABILITIES_RSP     = 0x2F 
    LL_CS_CONFIG_REQ           = 0x30 
    LL_CS_CONFIG_RSP           = 0x31 
    LL_CS_REQ                  = 0x32 
    LL_CS_RSP                  = 0x33 
    LL_CS_IND                  = 0x34 
    LL_CS_TERMINATE_REQ        = 0x35 
    LL_CS_FAE_REQ              = 0x36 
    LL_CS_FAE_RSP              = 0x37 
    LL_CS_CHANNEL_MAP_IND      = 0x38 
    LL_CS_SEC_REQ              = 0x39 
    LL_CS_TERMINATE_RSP        = 0x3A
    LL_FRAME_SPACE_REQ         = 0x3B
    LL_FRAME_SPACE_RSP         = 0x3C
    # fmt: on


# TODO: Implement serialization.
class ControlPdu:
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4. Data Physical Channel PDU.
    '''

    opcode: Opcode


@dataclass
class LLTerminateInd(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.3. LL_TERMINATE_IND.
    '''

    opcode = Opcode.LL_TERMINATE_IND

    error_code: int


@dataclass
class LLEncReq(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.4. LL_ENC_REQ.
    '''

    opcode = Opcode.LL_ENC_REQ

    rand: bytes
    ediv: int
    skd_c: bytes
    iv_c: int


@dataclass
class LLEncRsp(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.5. LL_ENC_RSP.
    '''

    opcode = Opcode.LL_ENC_RSP

    skd_p: bytes
    iv_p: int


@dataclass
class LLStartEncReq(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.6. LL_START_ENC_REQ.
    '''

    opcode = Opcode.LL_START_ENC_REQ


@dataclass
class LLStartEncRsp(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.7. LL_START_ENC_RSP.
    '''

    opcode = Opcode.LL_START_ENC_RSP


@dataclass
class LLCisReq(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.29. LL_CIS_REQ.
    '''

    opcode = Opcode.LL_CIS_REQ

    cig_id: int
    cis_id: int
    phy_c_to_p: int
    phy_p_to_c: int
    max_sdu_c_to_p: int
    framing_mode: int
    max_sdu_p_to_c: int
    sdu_interval_c_to_p: int
    sdu_interval_p_to_c: int
    max_pdu_c_to_p: int
    max_pdu_p_to_c: int
    nse: int
    sub_interval: int
    bn_c_to_p: int
    bn_p_to_c: int
    ft_c_to_p: int
    ft_p_to_c: int
    iso_interval: int
    cis_offset_min: int
    cis_offset_max: int
    conn_event_count: int


@dataclass
class LLCisRsp(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.30. LL_CIS_RSP.
    '''

    opcode = Opcode.LL_CIS_RSP

    cis_offset_min: int = 0
    cis_offset_max: int = 0
    conn_event_count: int = 0


@dataclass
class LLCisInd(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.31. LL_CIS_IND.
    '''

    opcode = Opcode.LL_CIS_IND

    access_address: bytes
    cis_offset: int = 0
    cig_sync_delay: int = 0
    cis_sync_delay: int = 0
    conn_event_count: int = 0


@dataclass
class LLCisTerminateInd(ControlPdu):
    '''
    See Bluetooth spec @ Vol 6, Part B - 2.4.2.32. LL_CIS_TERMINATE_IND.
    '''

    opcode = Opcode.LL_CIS_TERMINATE_IND

    # access_address
    cig_id: int
    cis_id: int
    error_code: int
