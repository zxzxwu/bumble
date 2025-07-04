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
# GATT - Generic Attribute Profile
#
# See Bluetooth spec @ Vol 3, Part G
#
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------
from __future__ import annotations
import enum
import functools
import logging
import struct
from typing import Iterable, Optional, Sequence, TypeVar, Union

from bumble.colors import color
from bumble.core import BaseBumbleError, UUID
from bumble.att import Attribute, AttributeValue

# -----------------------------------------------------------------------------
# Typing
# -----------------------------------------------------------------------------
_T = TypeVar('_T')

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# fmt: off
# pylint: disable=line-too-long

GATT_REQUEST_TIMEOUT = 30  # seconds

GATT_MAX_ATTRIBUTE_VALUE_SIZE = 512

# Services
GATT_GENERIC_ACCESS_SERVICE                 = UUID.from_16_bits(0x1800, 'Generic Access')
GATT_GENERIC_ATTRIBUTE_SERVICE              = UUID.from_16_bits(0x1801, 'Generic Attribute')
GATT_IMMEDIATE_ALERT_SERVICE                = UUID.from_16_bits(0x1802, 'Immediate Alert')
GATT_LINK_LOSS_SERVICE                      = UUID.from_16_bits(0x1803, 'Link Loss')
GATT_TX_POWER_SERVICE                       = UUID.from_16_bits(0x1804, 'TX Power')
GATT_CURRENT_TIME_SERVICE                   = UUID.from_16_bits(0x1805, 'Current Time')
GATT_REFERENCE_TIME_UPDATE_SERVICE          = UUID.from_16_bits(0x1806, 'Reference Time Update')
GATT_NEXT_DST_CHANGE_SERVICE                = UUID.from_16_bits(0x1807, 'Next DST Change')
GATT_GLUCOSE_SERVICE                        = UUID.from_16_bits(0x1808, 'Glucose')
GATT_HEALTH_THERMOMETER_SERVICE             = UUID.from_16_bits(0x1809, 'Health Thermometer')
GATT_DEVICE_INFORMATION_SERVICE             = UUID.from_16_bits(0x180A, 'Device Information')
GATT_HEART_RATE_SERVICE                     = UUID.from_16_bits(0x180D, 'Heart Rate')
GATT_PHONE_ALERT_STATUS_SERVICE             = UUID.from_16_bits(0x180E, 'Phone Alert Status')
GATT_BATTERY_SERVICE                        = UUID.from_16_bits(0x180F, 'Battery')
GATT_BLOOD_PRESSURE_SERVICE                 = UUID.from_16_bits(0x1810, 'Blood Pressure')
GATT_ALERT_NOTIFICATION_SERVICE             = UUID.from_16_bits(0x1811, 'Alert Notification')
GATT_HUMAN_INTERFACE_DEVICE_SERVICE         = UUID.from_16_bits(0x1812, 'Human Interface Device')
GATT_SCAN_PARAMETERS_SERVICE                = UUID.from_16_bits(0x1813, 'Scan Parameters')
GATT_RUNNING_SPEED_AND_CADENCE_SERVICE      = UUID.from_16_bits(0x1814, 'Running Speed and Cadence')
GATT_AUTOMATION_IO_SERVICE                  = UUID.from_16_bits(0x1815, 'Automation IO')
GATT_CYCLING_SPEED_AND_CADENCE_SERVICE      = UUID.from_16_bits(0x1816, 'Cycling Speed and Cadence')
GATT_CYCLING_POWER_SERVICE                  = UUID.from_16_bits(0x1818, 'Cycling Power')
GATT_LOCATION_AND_NAVIGATION_SERVICE        = UUID.from_16_bits(0x1819, 'Location and Navigation')
GATT_ENVIRONMENTAL_SENSING_SERVICE          = UUID.from_16_bits(0x181A, 'Environmental Sensing')
GATT_BODY_COMPOSITION_SERVICE               = UUID.from_16_bits(0x181B, 'Body Composition')
GATT_USER_DATA_SERVICE                      = UUID.from_16_bits(0x181C, 'User Data')
GATT_WEIGHT_SCALE_SERVICE                   = UUID.from_16_bits(0x181D, 'Weight Scale')
GATT_BOND_MANAGEMENT_SERVICE                = UUID.from_16_bits(0x181E, 'Bond Management')
GATT_CONTINUOUS_GLUCOSE_MONITORING_SERVICE  = UUID.from_16_bits(0x181F, 'Continuous Glucose Monitoring')
GATT_INTERNET_PROTOCOL_SUPPORT_SERVICE      = UUID.from_16_bits(0x1820, 'Internet Protocol Support')
GATT_INDOOR_POSITIONING_SERVICE             = UUID.from_16_bits(0x1821, 'Indoor Positioning')
GATT_PULSE_OXIMETER_SERVICE                 = UUID.from_16_bits(0x1822, 'Pulse Oximeter')
GATT_HTTP_PROXY_SERVICE                     = UUID.from_16_bits(0x1823, 'HTTP Proxy')
GATT_TRANSPORT_DISCOVERY_SERVICE            = UUID.from_16_bits(0x1824, 'Transport Discovery')
GATT_OBJECT_TRANSFER_SERVICE                = UUID.from_16_bits(0x1825, 'Object Transfer')
GATT_FITNESS_MACHINE_SERVICE                = UUID.from_16_bits(0x1826, 'Fitness Machine')
GATT_MESH_PROVISIONING_SERVICE              = UUID.from_16_bits(0x1827, 'Mesh Provisioning')
GATT_MESH_PROXY_SERVICE                     = UUID.from_16_bits(0x1828, 'Mesh Proxy')
GATT_RECONNECTION_CONFIGURATION_SERVICE     = UUID.from_16_bits(0x1829, 'Reconnection Configuration')
GATT_INSULIN_DELIVERY_SERVICE               = UUID.from_16_bits(0x183A, 'Insulin Delivery')
GATT_BINARY_SENSOR_SERVICE                  = UUID.from_16_bits(0x183B, 'Binary Sensor')
GATT_EMERGENCY_CONFIGURATION_SERVICE        = UUID.from_16_bits(0x183C, 'Emergency Configuration')
GATT_AUTHORIZATION_CONTROL_SERVICE          = UUID.from_16_bits(0x183D, 'Authorization Control')
GATT_PHYSICAL_ACTIVITY_MONITOR_SERVICE      = UUID.from_16_bits(0x183E, 'Physical Activity Monitor')
GATT_ELAPSED_TIME_SERVICE                   = UUID.from_16_bits(0x183F, 'Elapsed Time')
GATT_GENERIC_HEALTH_SENSOR_SERVICE          = UUID.from_16_bits(0x1840, 'Generic Health Sensor')
GATT_AUDIO_INPUT_CONTROL_SERVICE            = UUID.from_16_bits(0x1843, 'Audio Input Control')
GATT_VOLUME_CONTROL_SERVICE                 = UUID.from_16_bits(0x1844, 'Volume Control')
GATT_VOLUME_OFFSET_CONTROL_SERVICE          = UUID.from_16_bits(0x1845, 'Volume Offset Control')
GATT_COORDINATED_SET_IDENTIFICATION_SERVICE = UUID.from_16_bits(0x1846, 'Coordinated Set Identification')
GATT_DEVICE_TIME_SERVICE                    = UUID.from_16_bits(0x1847, 'Device Time')
GATT_MEDIA_CONTROL_SERVICE                  = UUID.from_16_bits(0x1848, 'Media Control')
GATT_GENERIC_MEDIA_CONTROL_SERVICE          = UUID.from_16_bits(0x1849, 'Generic Media Control')
GATT_CONSTANT_TONE_EXTENSION_SERVICE        = UUID.from_16_bits(0x184A, 'Constant Tone Extension')
GATT_TELEPHONE_BEARER_SERVICE               = UUID.from_16_bits(0x184B, 'Telephone Bearer')
GATT_GENERIC_TELEPHONE_BEARER_SERVICE       = UUID.from_16_bits(0x184C, 'Generic Telephone Bearer')
GATT_MICROPHONE_CONTROL_SERVICE             = UUID.from_16_bits(0x184D, 'Microphone Control')
GATT_AUDIO_STREAM_CONTROL_SERVICE           = UUID.from_16_bits(0x184E, 'Audio Stream Control')
GATT_BROADCAST_AUDIO_SCAN_SERVICE           = UUID.from_16_bits(0x184F, 'Broadcast Audio Scan')
GATT_PUBLISHED_AUDIO_CAPABILITIES_SERVICE   = UUID.from_16_bits(0x1850, 'Published Audio Capabilities')
GATT_BASIC_AUDIO_ANNOUNCEMENT_SERVICE       = UUID.from_16_bits(0x1851, 'Basic Audio Announcement')
GATT_BROADCAST_AUDIO_ANNOUNCEMENT_SERVICE   = UUID.from_16_bits(0x1852, 'Broadcast Audio Announcement')
GATT_COMMON_AUDIO_SERVICE                   = UUID.from_16_bits(0x1853, 'Common Audio')
GATT_HEARING_ACCESS_SERVICE                 = UUID.from_16_bits(0x1854, 'Hearing Access')
GATT_TELEPHONY_AND_MEDIA_AUDIO_SERVICE      = UUID.from_16_bits(0x1855, 'Telephony and Media Audio')
GATT_PUBLIC_BROADCAST_ANNOUNCEMENT_SERVICE  = UUID.from_16_bits(0x1856, 'Public Broadcast Announcement')
GATT_ELECTRONIC_SHELF_LABEL_SERVICE         = UUID.from_16_bits(0X1857, 'Electronic Shelf Label')
GATT_GAMING_AUDIO_SERVICE                   = UUID.from_16_bits(0x1858, 'Gaming Audio')
GATT_MESH_PROXY_SOLICITATION_SERVICE        = UUID.from_16_bits(0x1859, 'Mesh Audio Solicitation')

# Attribute Types
GATT_PRIMARY_SERVICE_ATTRIBUTE_TYPE   = UUID.from_16_bits(0x2800, 'Primary Service')
GATT_SECONDARY_SERVICE_ATTRIBUTE_TYPE = UUID.from_16_bits(0x2801, 'Secondary Service')
GATT_INCLUDE_ATTRIBUTE_TYPE           = UUID.from_16_bits(0x2802, 'Include')
GATT_CHARACTERISTIC_ATTRIBUTE_TYPE    = UUID.from_16_bits(0x2803, 'Characteristic')

# Descriptors
GATT_CHARACTERISTIC_EXTENDED_PROPERTIES_DESCRIPTOR   = UUID.from_16_bits(0x2900, 'Characteristic Extended Properties')
GATT_CHARACTERISTIC_USER_DESCRIPTION_DESCRIPTOR      = UUID.from_16_bits(0x2901, 'Characteristic User Description')
GATT_CLIENT_CHARACTERISTIC_CONFIGURATION_DESCRIPTOR  = UUID.from_16_bits(0x2902, 'Client Characteristic Configuration')
GATT_SERVER_CHARACTERISTIC_CONFIGURATION_DESCRIPTOR  = UUID.from_16_bits(0x2903, 'Server Characteristic Configuration')
GATT_CHARACTERISTIC_PRESENTATION_FORMAT_DESCRIPTOR   = UUID.from_16_bits(0x2904, 'Characteristic Format')
GATT_CHARACTERISTIC_AGGREGATE_FORMAT_DESCRIPTOR      = UUID.from_16_bits(0x2905, 'Characteristic Aggregate Format')
GATT_VALID_RANGE_DESCRIPTOR                          = UUID.from_16_bits(0x2906, 'Valid Range')
GATT_EXTERNAL_REPORT_DESCRIPTOR                      = UUID.from_16_bits(0x2907, 'External Report')
GATT_REPORT_REFERENCE_DESCRIPTOR                     = UUID.from_16_bits(0x2908, 'Report Reference')
GATT_NUMBER_OF_DIGITALS_DESCRIPTOR                   = UUID.from_16_bits(0x2909, 'Number of Digitals')
GATT_VALUE_TRIGGER_SETTING_DESCRIPTOR                = UUID.from_16_bits(0x290A, 'Value Trigger Setting')
GATT_ENVIRONMENTAL_SENSING_CONFIGURATION_DESCRIPTOR  = UUID.from_16_bits(0x290B, 'Environmental Sensing Configuration')
GATT_ENVIRONMENTAL_SENSING_MEASUREMENT_DESCRIPTOR    = UUID.from_16_bits(0x290C, 'Environmental Sensing Measurement')
GATT_ENVIRONMENTAL_SENSING_TRIGGER_DESCRIPTOR        = UUID.from_16_bits(0x290D, 'Environmental Sensing Trigger Setting')
GATT_TIME_TRIGGER_DESCRIPTOR                         = UUID.from_16_bits(0x290E, 'Time Trigger Setting')
GATT_COMPLETE_BR_EDR_TRANSPORT_BLOCK_DATA_DESCRIPTOR = UUID.from_16_bits(0x290F, 'Complete BR-EDR Transport Block Data')
GATT_OBSERVATION_SCHEDULE_DESCRIPTOR                 = UUID.from_16_bits(0x290F, 'Observation Schedule')
GATT_VALID_RANGE_AND_ACCURACY_DESCRIPTOR             = UUID.from_16_bits(0x290F, 'Valid Range And Accuracy')

# Device Information Service
GATT_SYSTEM_ID_CHARACTERISTIC                          = UUID.from_16_bits(0x2A23, 'System ID')
GATT_MODEL_NUMBER_STRING_CHARACTERISTIC                = UUID.from_16_bits(0x2A24, 'Model Number String')
GATT_SERIAL_NUMBER_STRING_CHARACTERISTIC               = UUID.from_16_bits(0x2A25, 'Serial Number String')
GATT_FIRMWARE_REVISION_STRING_CHARACTERISTIC           = UUID.from_16_bits(0x2A26, 'Firmware Revision String')
GATT_HARDWARE_REVISION_STRING_CHARACTERISTIC           = UUID.from_16_bits(0x2A27, 'Hardware Revision String')
GATT_SOFTWARE_REVISION_STRING_CHARACTERISTIC           = UUID.from_16_bits(0x2A28, 'Software Revision String')
GATT_MANUFACTURER_NAME_STRING_CHARACTERISTIC           = UUID.from_16_bits(0x2A29, 'Manufacturer Name String')
GATT_REGULATORY_CERTIFICATION_DATA_LIST_CHARACTERISTIC = UUID.from_16_bits(0x2A2A, 'IEEE 11073-20601 Regulatory Certification Data List')
GATT_PNP_ID_CHARACTERISTIC                             = UUID.from_16_bits(0x2A50, 'PnP ID')

# Human Interface Device Service
GATT_HID_INFORMATION_CHARACTERISTIC   = UUID.from_16_bits(0x2A4A, 'HID Information')
GATT_REPORT_MAP_CHARACTERISTIC        = UUID.from_16_bits(0x2A4B, 'Report Map')
GATT_HID_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2A4C, 'HID Control Point')
GATT_REPORT_CHARACTERISTIC            = UUID.from_16_bits(0x2A4D, 'Report')
GATT_PROTOCOL_MODE_CHARACTERISTIC     = UUID.from_16_bits(0x2A4E, 'Protocol Mode')

# Heart Rate Service
GATT_HEART_RATE_MEASUREMENT_CHARACTERISTIC   = UUID.from_16_bits(0x2A37, 'Heart Rate Measurement')
GATT_BODY_SENSOR_LOCATION_CHARACTERISTIC     = UUID.from_16_bits(0x2A38, 'Body Sensor Location')
GATT_HEART_RATE_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2A39, 'Heart Rate Control Point')

# Battery Service
GATT_BATTERY_LEVEL_CHARACTERISTIC = UUID.from_16_bits(0x2A19, 'Battery Level')

# Telephony And Media Audio Service (TMAS)
GATT_TMAP_ROLE_CHARACTERISTIC = UUID.from_16_bits(0x2B51, 'TMAP Role')

# Audio Input Control Service (AICS)
GATT_AUDIO_INPUT_STATE_CHARACTERISTIC         = UUID.from_16_bits(0x2B77, 'Audio Input State')
GATT_GAIN_SETTINGS_ATTRIBUTE_CHARACTERISTIC   = UUID.from_16_bits(0x2B78, 'Gain Settings Attribute')
GATT_AUDIO_INPUT_TYPE_CHARACTERISTIC          = UUID.from_16_bits(0x2B79, 'Audio Input Type')
GATT_AUDIO_INPUT_STATUS_CHARACTERISTIC        = UUID.from_16_bits(0x2B7A, 'Audio Input Status')
GATT_AUDIO_INPUT_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2B7B, 'Audio Input Control Point')
GATT_AUDIO_INPUT_DESCRIPTION_CHARACTERISTIC   = UUID.from_16_bits(0x2B7C, 'Audio Input Description')

# Volume Control Service (VCS)
GATT_VOLUME_STATE_CHARACTERISTIC                = UUID.from_16_bits(0x2B7D, 'Volume State')
GATT_VOLUME_CONTROL_POINT_CHARACTERISTIC        = UUID.from_16_bits(0x2B7E, 'Volume Control Point')
GATT_VOLUME_FLAGS_CHARACTERISTIC                = UUID.from_16_bits(0x2B7F, 'Volume Flags')

# Volume Offset Control Service (VOCS)
GATT_VOLUME_OFFSET_STATE_CHARACTERISTIC         = UUID.from_16_bits(0x2B80, 'Volume Offset State')
GATT_AUDIO_LOCATION_CHARACTERISTIC              = UUID.from_16_bits(0x2B81, 'Audio Location')
GATT_VOLUME_OFFSET_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2B82, 'Volume Offset Control Point')
GATT_AUDIO_OUTPUT_DESCRIPTION_CHARACTERISTIC    = UUID.from_16_bits(0x2B83, 'Audio Output Description')

# Coordinated Set Identification Service (CSIS)
GATT_SET_IDENTITY_RESOLVING_KEY_CHARACTERISTIC  = UUID.from_16_bits(0x2B84, 'Set Identity Resolving Key')
GATT_COORDINATED_SET_SIZE_CHARACTERISTIC        = UUID.from_16_bits(0x2B85, 'Coordinated Set Size')
GATT_SET_MEMBER_LOCK_CHARACTERISTIC             = UUID.from_16_bits(0x2B86, 'Set Member Lock')
GATT_SET_MEMBER_RANK_CHARACTERISTIC             = UUID.from_16_bits(0x2B87, 'Set Member Rank')

# Media Control Service (MCS)
GATT_MEDIA_PLAYER_NAME_CHARACTERISTIC                     = UUID.from_16_bits(0x2B93, 'Media Player Name')
GATT_MEDIA_PLAYER_ICON_OBJECT_ID_CHARACTERISTIC           = UUID.from_16_bits(0x2B94, 'Media Player Icon Object ID')
GATT_MEDIA_PLAYER_ICON_URL_CHARACTERISTIC                 = UUID.from_16_bits(0x2B95, 'Media Player Icon URL')
GATT_TRACK_CHANGED_CHARACTERISTIC                         = UUID.from_16_bits(0x2B96, 'Track Changed')
GATT_TRACK_TITLE_CHARACTERISTIC                           = UUID.from_16_bits(0x2B97, 'Track Title')
GATT_TRACK_DURATION_CHARACTERISTIC                        = UUID.from_16_bits(0x2B98, 'Track Duration')
GATT_TRACK_POSITION_CHARACTERISTIC                        = UUID.from_16_bits(0x2B99, 'Track Position')
GATT_PLAYBACK_SPEED_CHARACTERISTIC                        = UUID.from_16_bits(0x2B9A, 'Playback Speed')
GATT_SEEKING_SPEED_CHARACTERISTIC                         = UUID.from_16_bits(0x2B9B, 'Seeking Speed')
GATT_CURRENT_TRACK_SEGMENTS_OBJECT_ID_CHARACTERISTIC      = UUID.from_16_bits(0x2B9C, 'Current Track Segments Object ID')
GATT_CURRENT_TRACK_OBJECT_ID_CHARACTERISTIC               = UUID.from_16_bits(0x2B9D, 'Current Track Object ID')
GATT_NEXT_TRACK_OBJECT_ID_CHARACTERISTIC                  = UUID.from_16_bits(0x2B9E, 'Next Track Object ID')
GATT_PARENT_GROUP_OBJECT_ID_CHARACTERISTIC                = UUID.from_16_bits(0x2B9F, 'Parent Group Object ID')
GATT_CURRENT_GROUP_OBJECT_ID_CHARACTERISTIC               = UUID.from_16_bits(0x2BA0, 'Current Group Object ID')
GATT_PLAYING_ORDER_CHARACTERISTIC                         = UUID.from_16_bits(0x2BA1, 'Playing Order')
GATT_PLAYING_ORDERS_SUPPORTED_CHARACTERISTIC              = UUID.from_16_bits(0x2BA2, 'Playing Orders Supported')
GATT_MEDIA_STATE_CHARACTERISTIC                           = UUID.from_16_bits(0x2BA3, 'Media State')
GATT_MEDIA_CONTROL_POINT_CHARACTERISTIC                   = UUID.from_16_bits(0x2BA4, 'Media Control Point')
GATT_MEDIA_CONTROL_POINT_OPCODES_SUPPORTED_CHARACTERISTIC = UUID.from_16_bits(0x2BA5, 'Media Control Point Opcodes Supported')
GATT_SEARCH_RESULTS_OBJECT_ID_CHARACTERISTIC              = UUID.from_16_bits(0x2BA6, 'Search Results Object ID')
GATT_SEARCH_CONTROL_POINT_CHARACTERISTIC                  = UUID.from_16_bits(0x2BA7, 'Search Control Point')
GATT_CONTENT_CONTROL_ID_CHARACTERISTIC                    = UUID.from_16_bits(0x2BBA, 'Content Control Id')

# Telephone Bearer Service (TBS)
GATT_BEARER_PROVIDER_NAME_CHARACTERISTIC                      = UUID.from_16_bits(0x2BB3, 'Bearer Provider Name')
GATT_BEARER_UCI_CHARACTERISTIC                                = UUID.from_16_bits(0x2BB4, 'Bearer UCI')
GATT_BEARER_TECHNOLOGY_CHARACTERISTIC                         = UUID.from_16_bits(0x2BB5, 'Bearer Technology')
GATT_BEARER_URI_SCHEMES_SUPPORTED_LIST_CHARACTERISTIC         = UUID.from_16_bits(0x2BB6, 'Bearer URI Schemes Supported List')
GATT_BEARER_SIGNAL_STRENGTH_CHARACTERISTIC                    = UUID.from_16_bits(0x2BB7, 'Bearer Signal Strength')
GATT_BEARER_SIGNAL_STRENGTH_REPORTING_INTERVAL_CHARACTERISTIC = UUID.from_16_bits(0x2BB8, 'Bearer Signal Strength Reporting Interval')
GATT_BEARER_LIST_CURRENT_CALLS_CHARACTERISTIC                 = UUID.from_16_bits(0x2BB9, 'Bearer List Current Calls')
GATT_CONTENT_CONTROL_ID_CHARACTERISTIC                        = UUID.from_16_bits(0x2BBA, 'Content Control ID')
GATT_STATUS_FLAGS_CHARACTERISTIC                              = UUID.from_16_bits(0x2BBB, 'Status Flags')
GATT_INCOMING_CALL_TARGET_BEARER_URI_CHARACTERISTIC           = UUID.from_16_bits(0x2BBC, 'Incoming Call Target Bearer URI')
GATT_CALL_STATE_CHARACTERISTIC                                = UUID.from_16_bits(0x2BBD, 'Call State')
GATT_CALL_CONTROL_POINT_CHARACTERISTIC                        = UUID.from_16_bits(0x2BBE, 'Call Control Point')
GATT_CALL_CONTROL_POINT_OPTIONAL_OPCODES_CHARACTERISTIC       = UUID.from_16_bits(0x2BBF, 'Call Control Point Optional Opcodes')
GATT_TERMINATION_REASON_CHARACTERISTIC                        = UUID.from_16_bits(0x2BC0, 'Termination Reason')
GATT_INCOMING_CALL_CHARACTERISTIC                             = UUID.from_16_bits(0x2BC1, 'Incoming Call')
GATT_CALL_FRIENDLY_NAME_CHARACTERISTIC                        = UUID.from_16_bits(0x2BC2, 'Call Friendly Name')

# Microphone Control Service (MICS)
GATT_MUTE_CHARACTERISTIC = UUID.from_16_bits(0x2BC3, 'Mute')

# Audio Stream Control Service (ASCS)
GATT_SINK_ASE_CHARACTERISTIC                    = UUID.from_16_bits(0x2BC4, 'Sink ASE')
GATT_SOURCE_ASE_CHARACTERISTIC                  = UUID.from_16_bits(0x2BC5, 'Source ASE')
GATT_ASE_CONTROL_POINT_CHARACTERISTIC           = UUID.from_16_bits(0x2BC6, 'ASE Control Point')

# Broadcast Audio Scan Service (BASS)
GATT_BROADCAST_AUDIO_SCAN_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2BC7, 'Broadcast Audio Scan Control Point')
GATT_BROADCAST_RECEIVE_STATE_CHARACTERISTIC            = UUID.from_16_bits(0x2BC8, 'Broadcast Receive State')

# Published Audio Capabilities Service (PACS)
GATT_SINK_PAC_CHARACTERISTIC                    = UUID.from_16_bits(0x2BC9, 'Sink PAC')
GATT_SINK_AUDIO_LOCATION_CHARACTERISTIC         = UUID.from_16_bits(0x2BCA, 'Sink Audio Location')
GATT_SOURCE_PAC_CHARACTERISTIC                  = UUID.from_16_bits(0x2BCB, 'Source PAC')
GATT_SOURCE_AUDIO_LOCATION_CHARACTERISTIC       = UUID.from_16_bits(0x2BCC, 'Source Audio Location')
GATT_AVAILABLE_AUDIO_CONTEXTS_CHARACTERISTIC    = UUID.from_16_bits(0x2BCD, 'Available Audio Contexts')
GATT_SUPPORTED_AUDIO_CONTEXTS_CHARACTERISTIC    = UUID.from_16_bits(0x2BCE, 'Supported Audio Contexts')

# Gaming Audio Service (GMAS)
GATT_GMAP_ROLE_CHARACTERISTIC    = UUID.from_16_bits(0x2C00, 'GMAP Role')
GATT_UGG_FEATURES_CHARACTERISTIC = UUID.from_16_bits(0x2C01, 'UGG Features')
GATT_UGT_FEATURES_CHARACTERISTIC = UUID.from_16_bits(0x2C02, 'UGT Features')
GATT_BGS_FEATURES_CHARACTERISTIC = UUID.from_16_bits(0x2C03, 'BGS Features')
GATT_BGR_FEATURES_CHARACTERISTIC = UUID.from_16_bits(0x2C04, 'BGR Features')

# Hearing Access Service
GATT_HEARING_AID_FEATURES_CHARACTERISTIC             = UUID.from_16_bits(0x2BDA, 'Hearing Aid Features')
GATT_HEARING_AID_PRESET_CONTROL_POINT_CHARACTERISTIC = UUID.from_16_bits(0x2BDB, 'Hearing Aid Preset Control Point')
GATT_ACTIVE_PRESET_INDEX_CHARACTERISTIC              = UUID.from_16_bits(0x2BDC, 'Active Preset Index')

# ASHA Service
GATT_ASHA_SERVICE                             = UUID.from_16_bits(0xFDF0, 'Audio Streaming for Hearing Aid')
GATT_ASHA_READ_ONLY_PROPERTIES_CHARACTERISTIC = UUID('6333651e-c481-4a3e-9169-7c902aad37bb', 'ReadOnlyProperties')
GATT_ASHA_AUDIO_CONTROL_POINT_CHARACTERISTIC  = UUID('f0d4de7e-4a88-476c-9d9f-1937b0996cc0', 'AudioControlPoint')
GATT_ASHA_AUDIO_STATUS_CHARACTERISTIC         = UUID('38663f1a-e711-4cac-b641-326b56404837', 'AudioStatus')
GATT_ASHA_VOLUME_CHARACTERISTIC               = UUID('00e4ca9e-ab14-41e4-8823-f9e70c7e91df', 'Volume')
GATT_ASHA_LE_PSM_OUT_CHARACTERISTIC           = UUID('2d410339-82b6-42aa-b34e-e2e01df8cc1a', 'LE_PSM_OUT')

# Apple Notification Center Service
GATT_ANCS_SERVICE                            = UUID('7905F431-B5CE-4E99-A40F-4B1E122D00D0', 'Apple Notification Center')
GATT_ANCS_NOTIFICATION_SOURCE_CHARACTERISTIC = UUID('9FBF120D-6301-42D9-8C58-25E699A21DBD', 'Notification Source')
GATT_ANCS_CONTROL_POINT_CHARACTERISTIC       = UUID('69D1D8F3-45E1-49A8-9821-9BBDFDAAD9D9', 'Control Point')
GATT_ANCS_DATA_SOURCE_CHARACTERISTIC         = UUID('22EAC6E9-24D6-4BB5-BE44-B36ACE7C7BFB', 'Data Source')

# Apple Media Service
GATT_AMS_SERVICE                         = UUID('89D3502B-0F36-433A-8EF4-C502AD55F8DC', 'Apple Media')
GATT_AMS_REMOTE_COMMAND_CHARACTERISTIC   = UUID('9B3C81D8-57B1-4A8A-B8DF-0E56F7CA51C2', 'Remote Command')
GATT_AMS_ENTITY_UPDATE_CHARACTERISTIC    = UUID('2F7CABCE-808D-411F-9A0C-BB92BA96C102', 'Entity Update')
GATT_AMS_ENTITY_ATTRIBUTE_CHARACTERISTIC = UUID('C6B2F38C-23AB-46D8-A6AB-A3A870BBD5D7', 'Entity Attribute')

# Misc Apple Services
GATT_APPLE_CONTINUITY_SERVICE = UUID('D0611E78-BBB4-4591-A5F8-487910AE4366', 'Apple Continuity')
GATT_APPLE_NEARBY_SERVICE     = UUID('9FA480E0-4967-4542-9390-D343DC5D04AE', 'Apple Nearby')

# Misc
GATT_DEVICE_NAME_CHARACTERISTIC                                = UUID.from_16_bits(0x2A00, 'Device Name')
GATT_APPEARANCE_CHARACTERISTIC                                 = UUID.from_16_bits(0x2A01, 'Appearance')
GATT_PERIPHERAL_PRIVACY_FLAG_CHARACTERISTIC                    = UUID.from_16_bits(0x2A02, 'Peripheral Privacy Flag')
GATT_RECONNECTION_ADDRESS_CHARACTERISTIC                       = UUID.from_16_bits(0x2A03, 'Reconnection Address')
GATT_PERIPHERAL_PREFERRED_CONNECTION_PARAMETERS_CHARACTERISTIC = UUID.from_16_bits(0x2A04, 'Peripheral Preferred Connection Parameters')
GATT_SERVICE_CHANGED_CHARACTERISTIC                            = UUID.from_16_bits(0x2A05, 'Service Changed')
GATT_ALERT_LEVEL_CHARACTERISTIC                                = UUID.from_16_bits(0x2A06, 'Alert Level')
GATT_TX_POWER_LEVEL_CHARACTERISTIC                             = UUID.from_16_bits(0x2A07, 'Tx Power Level')
GATT_BOOT_KEYBOARD_INPUT_REPORT_CHARACTERISTIC                 = UUID.from_16_bits(0x2A22, 'Boot Keyboard Input Report')
GATT_CURRENT_TIME_CHARACTERISTIC                               = UUID.from_16_bits(0x2A2B, 'Current Time')
GATT_BOOT_KEYBOARD_OUTPUT_REPORT_CHARACTERISTIC                = UUID.from_16_bits(0x2A32, 'Boot Keyboard Output Report')
GATT_CENTRAL_ADDRESS_RESOLUTION__CHARACTERISTIC                = UUID.from_16_bits(0x2AA6, 'Central Address Resolution')
GATT_CLIENT_SUPPORTED_FEATURES_CHARACTERISTIC                  = UUID.from_16_bits(0x2B29, 'Client Supported Features')
GATT_DATABASE_HASH_CHARACTERISTIC                              = UUID.from_16_bits(0x2B2A, 'Database Hash')
GATT_SERVER_SUPPORTED_FEATURES_CHARACTERISTIC                  = UUID.from_16_bits(0x2B3A, 'Server Supported Features')
GATT_LE_GATT_SECURITY_LEVELS_CHARACTERISTIC                    = UUID.from_16_bits(0x2BF5, 'E GATT Security Levels')

# fmt: on
# pylint: enable=line-too-long


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------
def show_services(services: Iterable[Service]) -> None:
    for service in services:
        print(color(str(service), 'cyan'))

        for characteristic in service.characteristics:
            print(color('  ' + str(characteristic), 'magenta'))

            for descriptor in characteristic.descriptors:
                print(color('    ' + str(descriptor), 'green'))


# -----------------------------------------------------------------------------
class InvalidServiceError(BaseBumbleError):
    """The service is not compliant with the spec/profile"""


# -----------------------------------------------------------------------------
class Service(Attribute):
    '''
    See Vol 3, Part G - 3.1 SERVICE DEFINITION
    '''

    uuid: UUID
    characteristics: list[Characteristic]
    included_services: list[Service]

    def __init__(
        self,
        uuid: Union[str, UUID],
        characteristics: Iterable[Characteristic],
        primary=True,
        included_services: Iterable[Service] = (),
    ) -> None:
        # Convert the uuid to a UUID object if it isn't already
        if isinstance(uuid, str):
            uuid = UUID(uuid)

        super().__init__(
            (
                GATT_PRIMARY_SERVICE_ATTRIBUTE_TYPE
                if primary
                else GATT_SECONDARY_SERVICE_ATTRIBUTE_TYPE
            ),
            Attribute.READABLE,
            uuid.to_pdu_bytes(),
        )
        self.uuid = uuid
        self.included_services = list(included_services)
        self.characteristics = list(characteristics)
        self.primary = primary

    def get_advertising_data(self) -> Optional[bytes]:
        """
        Get Service specific advertising data
        Defined by each Service, default value is empty
        :return Service data for advertising
        """
        return None

    def __str__(self) -> str:
        return (
            f'Service(handle=0x{self.handle:04X}, '
            f'end=0x{self.end_group_handle:04X}, '
            f'uuid={self.uuid})'
            f'{"" if self.primary else "*"}'
        )


# -----------------------------------------------------------------------------
class TemplateService(Service):
    '''
    Convenience abstract class that can be used by profile-specific subclasses that want
    to expose their UUID as a class property
    '''

    UUID: UUID

    def __init__(
        self,
        characteristics: Iterable[Characteristic],
        primary: bool = True,
        included_services: Iterable[Service] = (),
    ) -> None:
        super().__init__(self.UUID, characteristics, primary, included_services)


# -----------------------------------------------------------------------------
class IncludedServiceDeclaration(Attribute):
    '''
    See Vol 3, Part G - 3.2 INCLUDE DEFINITION
    '''

    service: Service

    def __init__(self, service: Service) -> None:
        declaration_bytes = struct.pack(
            '<HH2s', service.handle, service.end_group_handle, bytes(service.uuid)
        )
        super().__init__(
            GATT_INCLUDE_ATTRIBUTE_TYPE, Attribute.READABLE, declaration_bytes
        )
        self.service = service

    def __str__(self) -> str:
        return (
            f'IncludedServiceDefinition(handle=0x{self.handle:04X}, '
            f'group_starting_handle=0x{self.service.handle:04X}, '
            f'group_ending_handle=0x{self.service.end_group_handle:04X}, '
            f'uuid={self.service.uuid})'
        )


# -----------------------------------------------------------------------------
class Characteristic(Attribute[_T]):
    '''
    See Vol 3, Part G - 3.3 CHARACTERISTIC DEFINITION
    '''

    uuid: UUID
    properties: Characteristic.Properties

    EVENT_SUBSCRIPTION = "subscription"

    class Properties(enum.IntFlag):
        """Property flags"""

        BROADCAST = 0x01
        READ = 0x02
        WRITE_WITHOUT_RESPONSE = 0x04
        WRITE = 0x08
        NOTIFY = 0x10
        INDICATE = 0x20
        AUTHENTICATED_SIGNED_WRITES = 0x40
        EXTENDED_PROPERTIES = 0x80

        @classmethod
        def from_string(cls, properties_str: str) -> Characteristic.Properties:
            try:
                return functools.reduce(
                    lambda x, y: x | cls[y],
                    properties_str.replace("|", ",").split(","),
                    Characteristic.Properties(0),
                )
            except (TypeError, KeyError):
                # The check for `p.name is not None` here is needed because for InFlag
                # enums, the .name property can be None, when the enum value is 0,
                # so the type hint for .name is Optional[str].
                enum_list: list[str] = [p.name for p in cls if p.name is not None]
                enum_list_str = ",".join(enum_list)
                raise TypeError(
                    f"Characteristic.Properties::from_string() error:\nExpected a string containing any of the keys, separated by , or |: {enum_list_str}\nGot: {properties_str}"
                )

        def __str__(self) -> str:
            # NOTE: we override this method to offer a consistent result between python
            # versions: the value returned by IntFlag.__str__() changed in version 11.
            return '|'.join(
                flag.name
                for flag in Characteristic.Properties
                if self.value & flag.value and flag.name is not None
            )

    # For backwards compatibility these are defined here
    # For new code, please use Characteristic.Properties.X
    BROADCAST = Properties.BROADCAST
    READ = Properties.READ
    WRITE_WITHOUT_RESPONSE = Properties.WRITE_WITHOUT_RESPONSE
    WRITE = Properties.WRITE
    NOTIFY = Properties.NOTIFY
    INDICATE = Properties.INDICATE
    AUTHENTICATED_SIGNED_WRITES = Properties.AUTHENTICATED_SIGNED_WRITES
    EXTENDED_PROPERTIES = Properties.EXTENDED_PROPERTIES

    def __init__(
        self,
        uuid: Union[str, bytes, UUID],
        properties: Characteristic.Properties,
        permissions: Union[str, Attribute.Permissions],
        value: Union[AttributeValue[_T], _T, None] = None,
        descriptors: Sequence[Descriptor] = (),
    ):
        super().__init__(uuid, permissions, value)
        self.uuid = self.type
        self.properties = properties
        self.descriptors = descriptors

    def get_descriptor(self, descriptor_type):
        for descriptor in self.descriptors:
            if descriptor.type == descriptor_type:
                return descriptor

        return None

    def has_properties(self, properties: Characteristic.Properties) -> bool:
        return self.properties & properties == properties

    def __str__(self) -> str:
        return (
            f'Characteristic(handle=0x{self.handle:04X}, '
            f'end=0x{self.end_group_handle:04X}, '
            f'uuid={self.uuid}, '
            f'{self.properties})'
        )


# -----------------------------------------------------------------------------
class CharacteristicDeclaration(Attribute):
    '''
    See Vol 3, Part G - 3.3.1 CHARACTERISTIC DECLARATION
    '''

    characteristic: Characteristic

    def __init__(
        self,
        characteristic: Characteristic,
        value_handle: int,
    ) -> None:
        declaration_bytes = (
            struct.pack('<BH', characteristic.properties, value_handle)
            + characteristic.uuid.to_pdu_bytes()
        )
        super().__init__(
            GATT_CHARACTERISTIC_ATTRIBUTE_TYPE, Attribute.READABLE, declaration_bytes
        )
        self.value_handle = value_handle
        self.characteristic = characteristic

    def __str__(self) -> str:
        return (
            f'CharacteristicDeclaration(handle=0x{self.handle:04X}, '
            f'value_handle=0x{self.value_handle:04X}, '
            f'uuid={self.characteristic.uuid}, '
            f'{self.characteristic.properties})'
        )


# -----------------------------------------------------------------------------
class CharacteristicValue(AttributeValue[_T]):
    """Same as AttributeValue, for backward compatibility"""


# -----------------------------------------------------------------------------
class Descriptor(Attribute):
    '''
    See Vol 3, Part G - 3.3.3 Characteristic Descriptor Declarations
    '''

    def __str__(self) -> str:
        if isinstance(self.value, bytes):
            value_str = self.value.hex()
        elif isinstance(self.value, CharacteristicValue):
            value_str = '<dynamic>'
        else:
            value_str = '<...>'
        return (
            f'Descriptor(handle=0x{self.handle:04X}, '
            f'type={self.type}, '
            f'value={value_str})'
        )


# -----------------------------------------------------------------------------
class ClientCharacteristicConfigurationBits(enum.IntFlag):
    '''
    See Vol 3, Part G - 3.3.3.3 - Table 3.11 Client Characteristic Configuration bit
    field definition
    '''

    DEFAULT = 0x0000
    NOTIFICATION = 0x0001
    INDICATION = 0x0002


# -----------------------------------------------------------------------------
class ClientSupportedFeatures(enum.IntFlag):
    '''
    See Vol 3, Part G - 7.2 - Table 7.6: Client Supported Features bit assignments.
    '''

    ROBUST_CACHING = 0x01
    ENHANCED_ATT_BEARER = 0x02
    MULTIPLE_HANDLE_VALUE_NOTIFICATIONS = 0x04


# -----------------------------------------------------------------------------
class ServerSupportedFeatures(enum.IntFlag):
    '''
    See Vol 3, Part G - 7.4 - Table 7.11: Server Supported Features bit assignments.
    '''

    EATT_SUPPORTED = 0x01
