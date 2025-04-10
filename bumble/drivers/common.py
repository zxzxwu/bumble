# Copyright 2021-2023 Google LLC
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
"""
Common types for drivers.
"""

# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------
import abc

from bumble import core


# -----------------------------------------------------------------------------
# Classes
# -----------------------------------------------------------------------------
class Driver(abc.ABC):
    """Base class for drivers."""

    @staticmethod
    async def for_host(_host):
        """Return a driver instance for a host.

        Args:
            host: Host object for which a driver should be created.

        Returns:
            A Driver instance if a driver should be instantiated for this host, or
            None if no driver instance of this class is needed.
        """
        return None

    @abc.abstractmethod
    async def init_controller(self):
        """Initialize the controller."""
