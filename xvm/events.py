from dataclasses import dataclass
from typing import (
    Tuple, List,
)

from lahja import (
    BaseEvent,
    ConnectionConfig,
)

from xvm.abc import ReceiptAPI
from xvm.rlp.receipts import Receipt
from xvm.vm.forks.xvm.computation import XvmComputation
from xvm.vm.message import Message


@dataclass
class EventBusConnected(BaseEvent):
    """
    Broadcasted when a new :class:`~lahja.endpoint.Endpoint` connects to the ``main``
    :class:`~lahja.endpoint.Endpoint`. The :class:`~lahja.endpoint.Endpoint` that connects to the
    the ``main`` :class:`~lahja.endpoint.Endpoint` should send
    :class:`~xvm.events.EventBusConnected` to ``main`` which will then cause ``main`` to send
    a :class:`~xvm.events.AvailableEndpointsUpdated` event to every connected
    :class:`~lahja.endpoint.Endpoint`, making them aware of other endpoints they can connect to.
    """

    connection_config: ConnectionConfig

@dataclass
class NewBlockImportStarted(BaseEvent):
    timestamp: int

@dataclass
class NewBlockImportFinished(BaseEvent):
    timestamp: int

@dataclass
class NewBlockImportCanceled(BaseEvent):
    timestamp: int
    reason: str

@dataclass
class AvailableEndpointsUpdated(BaseEvent):
    """
    Broadcasted by the ``main`` :class:`~lahja.endpoint.Endpoint` after it has received a
    :class:`~xvm.events.EventBusConnected` event. The ``available_endpoints`` property
    lists all available endpoints that are known at the time when the event is raised.
    """

    available_endpoints: Tuple[ConnectionConfig, ...]
