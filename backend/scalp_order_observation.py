"""Inactive testnet-only scalp evidence adapter. No trading or DB connection."""
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import re

from open_order_capture import _provider_identity


def observe(call, *, sink, network, environment, account_hash, symbol, slot_ms,
            intent, cloid, is_buy):
    """Invoke exactly once. Observer failure cannot interrupt protective orders.

    This is a separate namespace: scalp-BTC is never a signals.id UUID.
    Callers must explicitly inject a sink; nothing activates via environment.
    """
    valid = (
        callable(sink) and network == "testnet"
        and isinstance(symbol, str) and re.fullmatch(r"[A-Z0-9]{1,20}", symbol)
        and type(slot_ms) is int and slot_ms > 0
        and intent in ("open", "sl", "tp1", "tp2")
        and type(is_buy) is bool
        and isinstance(account_hash, str) and re.fullmatch(r"[a-f0-9]{64}", account_hash)
        and isinstance(environment, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", environment)
    )
    if not valid:
        return call()
    identity = f"scalp-{symbol}"
    expected = "0x" + hashlib.sha256(f"{identity}|{intent}|{slot_ms}".encode()).hexdigest()[:32]
    if cloid != expected:
        return call()
    correlation = str(uuid4())
    def emit(phase, **extra):
        event = dict(id=str(uuid4()), correlation_id=correlation,
                     environment=environment, network=network, account_hash=account_hash,
                     symbol=symbol, slot_ms=slot_ms, scalp_identity=identity,
                     intent=intent, client_order_id=cloid, intended_is_buy=is_buy,
                     phase=phase, observed_at=datetime.now(timezone.utc).isoformat(),
                     training_eligible=False, attribution_verified=False, **extra)
        try:
            sink(event)
        except Exception:
            pass
    emit("INTENT")
    try:
        response = call()
    except Exception:
        emit("UNKNOWN")
        raise
    emit("RESPONSE", provider=_provider_identity(response))
    return response


def persist(event, connection):
    """Append only, in a caller-owned transaction. Never creates a connection."""
    import json
    from sqlalchemy import text
    if (event.get("network") != "testnet" or event.get("training_eligible") is not False
            or event.get("attribution_verified") is not False
            or event.get("scalp_identity") != "scalp-" + event.get("symbol", "")):
        raise ValueError("INVALID_SCALP_EVIDENCE")
    connection.execute(text("""
        INSERT INTO scalp_order_observations
        (id,correlation_id,environment,network,account_hash,symbol,slot_ms,
         intent,phase,observed_at,payload,training_eligible)
        VALUES (:id,:correlation_id,:environment,:network,:account_hash,
                :symbol,:slot_ms,:intent,:phase,:observed_at,CAST(:payload AS jsonb),false)
    """), {**event, "payload": json.dumps(event, sort_keys=True)})


def order_wrappers(*, sink, environment, account_hash, symbol, slot_ms,
                   starts_at, ends_at, clock=None, account_guard=None):
    """Inactive, explicitly injected wrappers for one market scalp and its exits.

    The owner must supply the verified account scope and flush buffered events
    after protection. No credentials, database connection or activation here.
    """
    import hl_exchange as exchange
    if (starts_at.tzinfo is None or ends_at.tzinfo is None or
            not 0 < (ends_at-starts_at).total_seconds() <= 86400):
        raise ValueError("INVALID_CAPTURE_WINDOW")
    clock = clock or (lambda: datetime.now(timezone.utc))
    def scoped(call, intent, cloid, is_buy, env):
        if account_guard is not None:
            try:
                allowed = account_guard()
            except Exception:
                allowed = False
            if not allowed:
                return call()
        if not starts_at <= clock() < ends_at:
            return call()
        return observe(call, sink=sink, network=exchange.hl_account._env(env),
                       environment=environment, account_hash=account_hash,
                       symbol=symbol, slot_ms=slot_ms, intent=intent,
                       cloid=cloid, is_buy=is_buy)
    def opening(signal, **kwargs):
        if signal.get("id") != f"scalp-{symbol}" or signal.get("candle_ts") != slot_ms:
            return exchange.open_position(signal, **kwargs)
        def send(coin, is_buy, size, cloid, env, leverage):
            return scoped(lambda: exchange.send_market_open(coin,is_buy,size,cloid,env,leverage),
                          "open", cloid, is_buy, env)
        return exchange.open_position(signal, send_fn=send, **kwargs)
    def exits(*args, **kwargs):
        env = kwargs.get("env")
        return exchange.send_exit_orders(*args, **kwargs,
            scalp_order_observer=lambda call,intent,cloid,is_buy:
                scoped(call,intent,cloid,is_buy,env))
    return opening, exits


class BufferedCapture:
    """Single-use, inactive adapter around the ENTIRE protective workflow.

    call receives optional order wrappers; flush receives an immutable batch
    only after call has returned/raised. The owner must place all protection
    inside call and persist the batch atomically. No retries or activation.
    """

    def __init__(self, *, flush, **scope):
        self._scope = dict(scope)
        self._flush = flush
        self._used = False
        self.flush_status = "NOT_RUN"

    def run(self, call, *, account_state):
        if self._used:
            raise ValueError("CAPTURE_ALREADY_USED")
        self._used = True
        import hl_account
        events = []
        overflow = False

        def account_guard():
            address = hl_account.account_address()
            return (
                hl_account._env() == "testnet"
                and isinstance(address, str)
                and re.fullmatch(r"0x[0-9a-fA-F]{40}", address) is not None
                and isinstance(account_state, dict)
                and account_state.get("configured") is True
                and account_state.get("env") == "testnet"
                and str(account_state.get("address", "")).lower() == address.lower()
                and hashlib.sha256(address.lower().encode()).hexdigest()
                == self._scope.get("account_hash")
            )

        def buffer(event):
            nonlocal overflow
            if len(events) >= 8:
                overflow = True
                return
            events.append(event)

        try:
            wrappers = (order_wrappers(sink=buffer, account_guard=account_guard,
                                      **self._scope)
                        if account_guard() else None)
        except Exception:
            wrappers = None
        try:
            return call(wrappers)
        finally:
            if overflow:
                self.flush_status = "OVERFLOW"
            elif not events:
                self.flush_status = "EMPTY"
            else:
                try:
                    self._flush(tuple(events))
                    self.flush_status = "FLUSHED"
                except Exception:
                    self.flush_status = "FAILED"
