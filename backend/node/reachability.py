"""
Petals-style direct P2P reachability checks for DistribLLM.

The protocol asks an already reachable DHT peer to dial a temporary target
peer without using relays. A failed direct check lets serving nodes select
Hivemind AutoRelay instead of advertising unreachable private addresses.

Adapted from Petals' MIT-licensed reachability implementation:
https://github.com/bigscience-workshop/petals/blob/main/src/petals/server/reachability.py
Copyright (c) 2022 Petals authors and collaborators.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from functools import partial
from typing import Optional

from hivemind import DHT
from hivemind.dht import DHTNode
from hivemind.moe.client.remote_expert_worker import RemoteExpertWorker
from hivemind.p2p import P2P, P2PContext, PeerID, ServicerBase
from hivemind.proto import dht_pb2
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)

_PROBE_P2P_OPTIONS = {
    "dht_mode": "client",
    "use_relay": False,
    "auto_nat": False,
    "nat_port_map": False,
    "no_listen": True,
    "startup_timeout": 60,
}


class ReachabilityProtocol(ServicerBase):
    """Let DHT peers ask each other to test a peer's direct dialability."""

    def __init__(self, *, probe: Optional[P2P] = None, wait_timeout: float = 5.0):
        self.probe = probe
        self.wait_timeout = wait_timeout
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None

    async def call_check(
        self,
        remote_peer: PeerID,
        *,
        check_peer: PeerID,
    ) -> Optional[bool]:
        try:
            request = dht_pb2.PingRequest(
                peer=dht_pb2.NodeInfo(node_id=check_peer.to_bytes())
            )
            timeout = (
                self.wait_timeout
                if check_peer == remote_peer
                else self.wait_timeout * 2
            )
            response = await self.get_stub(
                self.probe,
                remote_peer,
            ).rpc_check(request, timeout=timeout)
            return bool(response.available)
        except Exception:
            logger.debug(
                "Peer %s could not check reachability for %s",
                remote_peer,
                check_peer,
                exc_info=True,
            )
            return None

    async def rpc_check(
        self,
        request: dht_pb2.PingRequest,
        context: P2PContext,
    ) -> dht_pb2.PingResponse:
        check_peer = PeerID(request.peer.node_id)
        available = True
        if check_peer != context.local_id:
            available = (
                await self.call_check(check_peer, check_peer=check_peer)
                is True
            )
        return dht_pb2.PingResponse(available=available)

    @asynccontextmanager
    async def serve(self, p2p: P2P):
        try:
            await self.add_p2p_handlers(p2p)
            yield self
        finally:
            await self.remove_p2p_handlers(p2p)

    @classmethod
    def attach_to_dht(
        cls,
        dht: DHT,
        *,
        await_ready: bool = False,
        **kwargs,
    ) -> "ReachabilityProtocol":
        protocol = cls(**kwargs)
        ready: Future[bool] = Future()

        async def _serve_with_probe() -> None:
            try:
                common_p2p = await dht.replicate_p2p()
                protocol._event_loop = asyncio.get_running_loop()
                protocol._stop = asyncio.Event()

                initial_peers = [
                    str(address)
                    for address in await common_p2p.get_visible_maddrs(
                        latest=True
                    )
                ]
                for peer in await common_p2p.list_peers():
                    initial_peers.extend(
                        f"{address}/p2p/{peer.peer_id}"
                        for address in peer.addrs
                    )

                protocol.probe = await P2P.create(
                    initial_peers,
                    **_PROBE_P2P_OPTIONS,
                )
                ready.set_result(True)
                async with protocol.serve(common_p2p):
                    await protocol._stop.wait()
            except Exception as e:
                logger.warning(
                    "Reachability service stopped unexpectedly: %s",
                    e,
                    exc_info=True,
                )
                if not ready.done():
                    ready.set_exception(e)
            finally:
                if protocol.probe is not None:
                    await protocol.probe.shutdown()

        threading.Thread(
            target=partial(asyncio.run, _serve_with_probe()),
            daemon=True,
            name="reachability-protocol",
        ).start()
        if await_ready:
            ready.result()
        return protocol

    def shutdown(self) -> None:
        if self._event_loop is not None and self._stop is not None:
            self._event_loop.call_soon_threadsafe(self._stop.set)


def check_direct_reachability(
    *,
    max_peers: int = 5,
    threshold: float = 0.5,
    **dht_options,
) -> Optional[bool]:
    """Return True/False for direct reachability, or None if no peer can test."""

    async def _check() -> Optional[bool]:
        target_dht = await DHTNode.create(client_mode=True, **dht_options)
        try:
            protocol = ReachabilityProtocol(probe=target_dht.protocol.p2p)
            async with protocol.serve(target_dht.protocol.p2p):
                successes = 0
                requests = 0
                remote_peers = list(
                    target_dht.protocol.routing_table.peer_id_to_uid.keys()
                )
                for remote_peer in remote_peers:
                    available = await protocol.call_check(
                        remote_peer=remote_peer,
                        check_peer=target_dht.peer_id,
                    )
                    if available is None:
                        continue
                    successes += int(available)
                    requests += 1
                    if requests >= max_peers:
                        break
            logger.info(
                "Direct P2P reachability checks succeeded for %s/%s peers",
                successes,
                requests,
            )
            return (
                (successes / requests) >= threshold
                if requests > 0
                else None
            )
        finally:
            await target_dht.shutdown()

    return RemoteExpertWorker.run_coroutine(_check())
