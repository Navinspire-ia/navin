"""Async message queue for decoupled channel-agent communication."""

import asyncio

from navin.bus.events import InboundMessage, OutboundMessage

DEFAULT_INBOUND_MAXSIZE = 1024
DEFAULT_OUTBOUND_MAXSIZE = 4096


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(
        self,
        *,
        inbound_maxsize: int = DEFAULT_INBOUND_MAXSIZE,
        outbound_maxsize: int = DEFAULT_OUTBOUND_MAXSIZE,
    ):
        """Create bounded queues so a stalled consumer cannot exhaust memory.

        ``asyncio.Queue.put`` supplies lossless backpressure to the normal
        publish methods. Legacy synchronous ``put_nowait`` publishers receive
        ``QueueFull`` instead of growing the gateway without a bound, making
        overload explicit and observable rather than an eventual OOM kill.
        Passing ``0`` deliberately restores asyncio's unbounded behavior for a
        narrowly scoped test or embedding.
        """
        if inbound_maxsize < 0 or outbound_maxsize < 0:
            raise ValueError("message bus queue sizes must be >= 0")
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(
            maxsize=inbound_maxsize
        )
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(
            maxsize=outbound_maxsize
        )
        self.inbound_backpressure_count = 0
        self.outbound_backpressure_count = 0

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        if self.inbound.full():
            self.inbound_backpressure_count += 1
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        if self.outbound.full():
            self.outbound_backpressure_count += 1
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()

    @property
    def inbound_capacity(self) -> int:
        """Maximum inbound depth (0 means deliberately unbounded)."""
        return self.inbound.maxsize

    @property
    def outbound_capacity(self) -> int:
        """Maximum outbound depth (0 means deliberately unbounded)."""
        return self.outbound.maxsize
