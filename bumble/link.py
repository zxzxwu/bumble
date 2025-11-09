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
from __future__ import annotations

import asyncio

# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------
import logging
from typing import Optional

from bumble import controller, core, hci, lmp, ll

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# TODO: add more support for various LL exchanges
# (see Vol 6, Part B - 2.4 DATA CHANNEL PDU)
# -----------------------------------------------------------------------------
class LocalLink:
    '''
    Link bus for controllers to communicate with each other
    '''

    controllers: set[controller.Controller]

    def __init__(self):
        self.controllers = set()
        self.pending_connection = None
        self.pending_classic_connection = None

    ############################################################
    # Common utils
    ############################################################

    def add_controller(self, controller: controller.Controller):
        logger.debug(f'new controller: {controller}')
        self.controllers.add(controller)

    def remove_controller(self, controller: controller.Controller):
        self.controllers.remove(controller)

    def find_controller(self, address: hci.Address) -> controller.Controller | None:
        for controller in self.controllers:
            if controller.random_address == address:
                return controller
        return None

    def find_classic_controller(
        self, address: hci.Address
    ) -> Optional[controller.Controller]:
        for controller in self.controllers:
            if controller.public_address == address:
                return controller
        return None

    def get_pending_connection(self):
        return self.pending_connection

    ############################################################
    # LE handlers
    ############################################################

    def on_address_changed(self, controller):
        pass

    def send_ll_control_pdu(
        self,
        sender_controller: controller.Controller,
        receiver_address: hci.Address,
        packet: ll.ControlPdu,
    ):
        if not (receiver_controller := self.find_controller(receiver_address)):
            raise core.InvalidArgumentError(
                f"Unable to find controller for address {receiver_address}"
            )
        asyncio.get_running_loop().call_soon(
            lambda: receiver_controller.on_ll_control_pdu(
                sender_controller.random_address, packet
            )
        )

    def send_advertising_data(self, sender_address: hci.Address, data: bytes):
        # Send the advertising data to all controllers, except the sender
        for controller in self.controllers:
            if controller.random_address != sender_address:
                controller.on_le_advertising_data(sender_address, data)

    def send_acl_data(
        self,
        sender_controller: controller.Controller,
        destination_address: hci.Address,
        transport: core.PhysicalTransport,
        data: bytes,
    ):
        # Send the data to the first controller with a matching address
        if transport == core.PhysicalTransport.LE:
            destination_controller = self.find_controller(destination_address)
            source_address = sender_controller.random_address
        elif transport == core.PhysicalTransport.BR_EDR:
            destination_controller = self.find_classic_controller(destination_address)
            source_address = sender_controller.public_address
        else:
            raise ValueError("unsupported transport type")

        if destination_controller is not None:
            asyncio.get_running_loop().call_soon(
                lambda: destination_controller.on_acl_data(
                    source_address, transport, data
                )
            )

    def on_connection_complete(self) -> None:
        # Check that we expect this call
        if not self.pending_connection:
            logger.warning('on_connection_complete with no pending connection')
            return

        central_address, le_create_connection_command = self.pending_connection
        self.pending_connection = None

        # Find the controller that initiated the connection
        if not (central_controller := self.find_controller(central_address)):
            logger.warning('!!! Initiating controller not found')
            return

        # Connect to the first controller with a matching address
        if peripheral_controller := self.find_controller(
            le_create_connection_command.peer_address
        ):
            central_controller.on_le_peripheral_connection_complete(
                le_create_connection_command, hci.HCI_SUCCESS
            )
            peripheral_controller.on_le_central_connected(central_address)
            return

        # No peripheral found
        central_controller.on_le_peripheral_connection_complete(
            le_create_connection_command, hci.HCI_CONNECTION_ACCEPT_TIMEOUT_ERROR
        )

    def connect(
        self,
        central_address: hci.Address,
        le_create_connection_command: hci.HCI_LE_Create_Connection_Command,
    ):
        logger.debug(
            f'$$$ CONNECTION {central_address} -> '
            f'{le_create_connection_command.peer_address}'
        )
        self.pending_connection = (central_address, le_create_connection_command)
        asyncio.get_running_loop().call_soon(self.on_connection_complete)

    ############################################################
    # Classic handlers
    ############################################################

    def send_lmp_packet(
        self,
        sender_controller: controller.Controller,
        receiver_address: hci.Address,
        packet: lmp.Packet,
    ):
        if not (receiver_controller := self.find_classic_controller(receiver_address)):
            raise core.InvalidArgumentError(
                f"Unable to find controller for address {receiver_address}"
            )
        asyncio.get_running_loop().call_soon(
            lambda: receiver_controller.on_lmp_packet(
                sender_controller.public_address, packet
            )
        )
