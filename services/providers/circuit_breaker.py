"""
Circuit Breaker pattern for provider fault tolerance.

Tracks provider health and temporarily disables providers that are
experiencing repeated failures, preventing cascading timeouts.

States:
- CLOSED: Normal operation, requests go through
- OPEN: Provider is failing, skip it for cooldown period
- HALF_OPEN: Cooldown expired, allow one test request

Usage:
    breaker = CircuitBreaker()

    if breaker.can_execute("alpaca"):
        try:
            result = provider.fetch_price(ticker)
            breaker.record_success("alpaca")
        except Exception:
            breaker.record_failure("alpaca")
"""

import time
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass, field
from threading import Lock


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal - allow requests
    OPEN = "open"          # Failing - block requests
    HALF_OPEN = "half_open"  # Testing - allow one request


@dataclass
class ProviderCircuit:
    """
    Tracks circuit state for a single provider.

    Attributes:
        state: Current circuit state
        failure_count: Number of failures in current window
        failure_timestamps: Times of recent failures (for windowed counting)
        last_failure_time: When the circuit was opened
        half_open_request_in_flight: Whether a test request is active
    """
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    failure_timestamps: list = field(default_factory=list)
    last_failure_time: float = 0
    last_success_time: float = 0
    half_open_request_in_flight: bool = False

    def reset(self):
        """Reset to closed state."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.failure_timestamps = []
        self.half_open_request_in_flight = False


class CircuitBreaker:
    """
    Circuit breaker for managing provider health.

    Dynamically tracks any provider by name. No hardcoded provider list.
    Thread-safe for concurrent access.

    Args:
        failure_threshold: Number of failures before opening circuit
        failure_window_seconds: Time window for counting failures
        cooldown_seconds: How long to wait before retrying open circuit
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        failure_window_seconds: float = 300,  # 5 minutes
        cooldown_seconds: float = 120,  # 2 minutes
    ):
        self.failure_threshold = failure_threshold
        self.failure_window_seconds = failure_window_seconds
        self.cooldown_seconds = cooldown_seconds

        # Provider name -> ProviderCircuit (dynamic, not hardcoded)
        self._circuits: Dict[str, ProviderCircuit] = {}
        self._lock = Lock()

    def _get_circuit(self, provider_name: str) -> ProviderCircuit:
        """Get or create circuit for a provider."""
        if provider_name not in self._circuits:
            self._circuits[provider_name] = ProviderCircuit()
        return self._circuits[provider_name]

    def _clean_old_failures(self, circuit: ProviderCircuit):
        """Remove failures outside the current window."""
        cutoff = time.time() - self.failure_window_seconds
        circuit.failure_timestamps = [
            ts for ts in circuit.failure_timestamps if ts > cutoff
        ]
        circuit.failure_count = len(circuit.failure_timestamps)

    def can_execute(self, provider_name: str) -> bool:
        """
        Check if a request to this provider should proceed.

        Returns:
            True if request should proceed, False if circuit is open
        """
        with self._lock:
            circuit = self._get_circuit(provider_name)
            now = time.time()

            if circuit.state == CircuitState.CLOSED:
                return True

            elif circuit.state == CircuitState.OPEN:
                # Check if cooldown has expired
                time_since_failure = now - circuit.last_failure_time

                if time_since_failure >= self.cooldown_seconds:
                    # Transition to half-open, allow one test request
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.half_open_request_in_flight = True
                    return True
                else:
                    # Still in cooldown
                    return False

            elif circuit.state == CircuitState.HALF_OPEN:
                # Only allow one request through at a time
                if not circuit.half_open_request_in_flight:
                    circuit.half_open_request_in_flight = True
                    return True
                else:
                    # Another request is already testing
                    return False

            return True  # Default allow

    def record_success(self, provider_name: str):
        """
        Record a successful request.

        Closes the circuit if it was half-open.
        """
        with self._lock:
            circuit = self._get_circuit(provider_name)
            circuit.last_success_time = time.time()

            if circuit.state == CircuitState.HALF_OPEN:
                # Test request succeeded, close the circuit
                circuit.reset()
            elif circuit.state == CircuitState.CLOSED:
                # Clean old failures from the window
                self._clean_old_failures(circuit)

    def record_failure(self, provider_name: str):
        """
        Record a failed request.

        May open the circuit if threshold is exceeded.
        """
        with self._lock:
            circuit = self._get_circuit(provider_name)
            now = time.time()

            if circuit.state == CircuitState.HALF_OPEN:
                # Test request failed, reopen circuit
                circuit.state = CircuitState.OPEN
                circuit.last_failure_time = now
                circuit.half_open_request_in_flight = False
                return

            # Add failure to window
            circuit.failure_timestamps.append(now)
            self._clean_old_failures(circuit)

            # Check if threshold exceeded
            if circuit.failure_count >= self.failure_threshold:
                circuit.state = CircuitState.OPEN
                circuit.last_failure_time = now

    def get_state(self, provider_name: str) -> CircuitState:
        """Get current state of a provider's circuit."""
        with self._lock:
            return self._get_circuit(provider_name).state

    def get_status(self, provider_name: str) -> Dict:
        """Get detailed status for a provider's circuit."""
        with self._lock:
            circuit = self._get_circuit(provider_name)
            now = time.time()

            status = {
                "provider": provider_name,
                "state": circuit.state.value,
                "failure_count": circuit.failure_count,
                "threshold": self.failure_threshold,
            }

            if circuit.state == CircuitState.OPEN:
                remaining = self.cooldown_seconds - (now - circuit.last_failure_time)
                status["cooldown_remaining_seconds"] = max(0, remaining)

            return status

    def get_all_status(self) -> Dict[str, Dict]:
        """Get status for all tracked providers."""
        with self._lock:
            return {
                name: self.get_status(name)
                for name in self._circuits
            }

    def reset_provider(self, provider_name: str):
        """Manually reset a provider's circuit to closed state."""
        with self._lock:
            if provider_name in self._circuits:
                self._circuits[provider_name].reset()

    def reset_all(self):
        """Reset all circuits to closed state."""
        with self._lock:
            for circuit in self._circuits.values():
                circuit.reset()


# Global instance
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get the global circuit breaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        # Import here to avoid circular imports
        from .config import get_config
        config = get_config()
        _circuit_breaker = CircuitBreaker(
            failure_threshold=config.failure_threshold,
            failure_window_seconds=config.failure_window_seconds,
            cooldown_seconds=config.cooldown_seconds,
        )
    return _circuit_breaker


def reset_circuit_breaker():
    """Reset the global circuit breaker (useful for config changes)."""
    global _circuit_breaker
    _circuit_breaker = None
