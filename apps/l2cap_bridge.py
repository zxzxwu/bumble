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
import asyncio
import click
import logging
import os
from colors import color

from bumble.transport import open_transport_or_link
from bumble.device import Device


# -----------------------------------------------------------------------------
class ServerBridge:
    def __init__(self, psm, tcp_host, tcp_port):
        self.psm      = psm
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port

    async def start(self, device):
        # Listen for incoming L2CAP CoC connections
        device.register_l2cap_le_coc_server(self.psm, self.on_coc)
        print(color(f'### Listening for CoC connection on PSM {self.psm}', 'yellow'))

        await device.start_advertising(auto_restart=True)

    # Called when a new L2CAP connection is established
    def on_coc(self, l2cap_channel):
        print(color('*** L2CAP channel:', 'cyan'), l2cap_channel)

        class Pipe:
            def __init__(self, bridge, l2cap_channel):
                self.bridge = bridge
                self.tcp_transport = None
                self.l2cap_channel = l2cap_channel
                l2cap_channel.on('close', self.on_l2cap_close)
                l2cap_channel.sink = self.on_coc_sdu

            async def connect_to_tcp(self):
                # Connect to the TCP server
                print(color(f'### Connecting to TCP {self.bridge.tcp_host}:{self.bridge.tcp_port}...', 'yellow'))

                class TcpClientProtocol(asyncio.Protocol):
                    def __init__(self, pipe):
                        self.pipe = pipe

                    def connection_lost(self, error):
                        print(color(f'!!! TCP connection lost: {error}', 'red'))
                        if self.pipe.l2cap_channel is not None:
                            asyncio.create_task(self.pipe.l2cap_channel.disconnect())

                    def data_received(self, data):
                        print(f'<<< Received on TCP: {data}')
                        self.pipe.l2cap_channel.write(data)

                try:
                    self.tcp_transport, _ = await asyncio.get_running_loop().create_connection(
                        lambda: TcpClientProtocol(self),
                        host=self.bridge.tcp_host,
                        port=self.bridge.tcp_port,
                    )
                    print(color('### Connected', 'green'))
                except Exception as error:
                    print(color(f'!!! Connection failed: {error}', 'red'))
                    await self.l2cap_channel.disconnect()

            def on_l2cap_close(self):
                self.l2cap_channel = None
                if self.tcp_transport is not None:
                    self.tcp_transport.close()

            def on_coc_sdu(self, sdu):
                print(color(f'<<< [L2CAP SDU]: {len(sdu)} bytes', 'cyan'))
                if self.tcp_transport is None:
                    print(color('!!! TCP socket not open, dropping', 'red'))
                    return
                self.tcp_transport.write(sdu)

        pipe = Pipe(self, l2cap_channel)

        asyncio.create_task(pipe.connect_to_tcp())


# -----------------------------------------------------------------------------
class ClientBridge:
    def __init__(self, psm, address, tcp_host, tcp_port):
        self.psm      = psm
        self.address  = address
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port

    async def start(self, device):
        print(color(f'### Connecting to {self.address}...', 'yellow'))
        connection = await device.connect(self.address)
        print(color('### Connected', 'green'))

        # Listen for TCP connections
        class TcpServerProtocol(asyncio.Protocol):
            def __init__(self, bridge):
                self.bridge = bridge

            # Called when a new connection is established
            def connection_made(self, transport):
                peername = transport.get_extra_info('peername')
                print(color(f'<<< TCP connection from {peername}', 'magenta'))
                self.tcp_transport = transport
                self.l2cap_channel = None
                asyncio.create_task(self.connect_to_l2cap())

            # Called when the client is disconnected
            def connection_lost(self, error):
                print(color(f'!!! TCP connection lost: {error}', 'red'))
                if self.l2cap_channel is not None:
                    asyncio.create_task(self.l2cap_channel.disconnect())

            def eof_received(self):
                pass

            # Called when data is received on the socket
            def data_received(self, data):
                print(f'<<< Received on TCP: {data}')
                if self.l2cap_channel is None:
                    print(color('!!! L2CAP channel not open, dropping', 'red'))
                    return

                self.l2cap_channel.write(data)

            async def connect_to_l2cap(self):
                def on_coc_sdu(sdu):
                    print(color(f'<<< [L2CAP SDU]: {len(sdu)} bytes', 'cyan'))
                    self.tcp_transport.write(sdu)

                print(color(f'>>> Opening L2CAP channel on PSM = {self.bridge.psm}', 'yellow'))
                try:
                    self.l2cap_channel = await connection.open_l2cap_coc(self.bridge.psm)
                    print(color('*** L2CAP channel:', 'cyan'), self.l2cap_channel)
                    self.l2cap_channel.sink = on_coc_sdu
                    self.l2cap_channel.on('close', self.on_l2cap_close)
                except Exception as error:
                    print(color(f'!!! Connection failed: {error}', 'red'))
                    self.tcp_transport.close()

            def on_l2cap_close(self):
                self.l2cap_channel = None
                self.tcp_transport.close()

        await asyncio.get_running_loop().create_server(
            lambda: TcpServerProtocol(self),
            host=self.tcp_host if self.tcp_host != '_' else None,
            port=self.tcp_port,
        )
        print(color(f'### Listening for TCP connections on port {self.tcp_port}', 'magenta'))


# -----------------------------------------------------------------------------
async def run(device_config, hci_transport, bridge):
    print('<<< connecting to HCI...')
    async with await open_transport_or_link(hci_transport) as (hci_source, hci_sink):
        print('<<< connected')

        device = Device.from_config_file_with_hci(device_config, hci_source, hci_sink)

        # Let's go
        await device.power_on()
        await bridge.start(device)

        # Wait until the transport terminates
        await hci_source.wait_for_termination()


# -----------------------------------------------------------------------------
@click.group()
@click.pass_context
@click.option('--device-config', help='Device configuration file', required=True)
@click.option('--hci-transport', help='HCI transport', required=True)
def cli(context, device_config, hci_transport):
    context.ensure_object(dict)
    context.obj['device_config'] = device_config
    context.obj['hci_transport'] = hci_transport


# -----------------------------------------------------------------------------
@cli.command()
@click.pass_context
@click.option('--psm', default=1234, help='PSM on which to accept client connections')
@click.option('--tcp-host', help='TCP host', default='localhost')
@click.option('--tcp-port', help='TCP port', default=9544)
def server(context, psm, tcp_host, tcp_port):
    bridge = ServerBridge(psm, tcp_host, tcp_port)
    asyncio.run(run(
        context.obj['device_config'],
        context.obj['hci_transport'],
        bridge
    ))


# -----------------------------------------------------------------------------
@cli.command()
@click.pass_context
@click.argument('bluetooth-address')
@click.option('--psm', default=1234, help='PSM on which to connect to the server')
@click.option('--tcp-host', help='TCP host', default='_')
@click.option('--tcp-port', help='TCP port', default=9543)
def client(context, bluetooth_address, psm, tcp_host, tcp_port):
    bridge = ClientBridge(psm, bluetooth_address, tcp_host, tcp_port)
    asyncio.run(run(
        context.obj['device_config'],
        context.obj['hci_transport'],
        bridge
    ))


# -----------------------------------------------------------------------------
logging.basicConfig(level = os.environ.get('BUMBLE_LOGLEVEL', 'WARNING').upper())
if __name__ == '__main__':
    cli(obj={})
