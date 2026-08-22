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

_DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class ReachabilityProtocol(ServicerBase):
    """Let DHT peers ask each other to test a peer's direct dialability."""

    def __init__(self, *, probe: Optional[P2P] = None, wait_timeout: float = 5.0):
        self.probe = probe
        self.wait_timeout = wait_timeout
        self._state_lock = threading.Lock()
        self._shutdown_requested = threading.Event()
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._shutdown_error: Optional[BaseException] = None
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
            event_loop: Optional[asyncio.AbstractEventLoop] = None
            stop_event: Optional[asyncio.Event] = None
            owned_probe: Optional[P2P] = None
            service_error: Optional[BaseException] = None
            try:
                if protocol._shutdown_requested.is_set():
                    return
                common_p2p = await dht.replicate_p2p()
                if protocol._shutdown_requested.is_set():
                    return
                event_loop = asyncio.get_running_loop()
                stop_event = asyncio.Event()
                with protocol._state_lock:
                    protocol._event_loop = event_loop
                    protocol._stop = stop_event
                    shutdown_requested = protocol._shutdown_requested.is_set()
                if shutdown_requested:
                    stop_event.set()
                    return

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

                owned_probe = await P2P.create(
                    initial_peers,
                    **_PROBE_P2P_OPTIONS,
                )
                protocol.probe = owned_probe
                if protocol._shutdown_requested.is_set():
                    return
                ready.set_result(True)
                async with protocol.serve(common_p2p):
                    await stop_event.wait()
            except Exception as e:
                service_error = e
                if protocol._shutdown_requested.is_set():
                    logger.debug(
                        "Reachability service stopped during shutdown: %s",
                        e,
                        exc_info=True,
                    )
                else:
                    logger.warning(
                        "Reachability service stopped unexpectedly: %s",
                        e,
                        exc_info=True,
                    )
            finally:
                if owned_probe is not None:
                    try:
                        await owned_probe.shutdown()
                    except BaseException as exc:
                        protocol._shutdown_error = exc
                        logger.warning(
                            "Reachability probe shutdown failed: %s",
                            exc,
                            exc_info=True,
                        )
                    else:
                        if protocol.probe is owned_probe:
                            protocol.probe = None
                with protocol._state_lock:
                    if protocol._event_loop is event_loop:
                        protocol._event_loop = None
                    if protocol._stop is stop_event:
                        protocol._stop = None
                protocol._stopped.set()
                if not ready.done():
                    ready.set_exception(
                        service_error
                        or RuntimeError(
                            "Reachability service stopped before startup completed"
                        )
                    )

        def _run() -> None:
            asyncio.run(_serve_with_probe())

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name="reachability-protocol",
        )
        protocol._thread = thread
        thread.start()
        if await_ready:
            ready.result()
        return protocol

    def shutdown(
        self,
        timeout: float = _DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> bool:
        """Request shutdown once and prove that the owned thread has exited."""
        self._shutdown_requested.set()
        with self._state_lock:
            event_loop = self._event_loop
            stop_event = self._stop
            thread = self._thread
        if event_loop is not None and stop_event is not None:
            try:
                event_loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                logger.debug(
                    "Reachability event loop closed while shutdown was requested",
                    exc_info=True,
                )
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        stopped = self._stopped.is_set() and not thread.is_alive()
        if not stopped:
            logger.warning(
                "Reachability service did not stop within %.3g seconds; "
                "retaining its exact startup handle",
                timeout,
            )
            return False
        if self._shutdown_error is not None:
            logger.warning(
                "Reachability service stopped with incomplete probe cleanup: %s",
                self._shutdown_error,
            )
            return False
        return True


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
