"""Verified repository identity and sanitized registration failures."""

from dataclasses import dataclass


class RegistrationError(RuntimeError):
    status_code = 400


class RegistrationDeniedError(RegistrationError):
    status_code = 403


class RegistrationNotFoundError(RegistrationDeniedError):
    status_code = 404


class RegistrationOperationalError(RegistrationError):
    status_code = 503


@dataclass(frozen=True)
class RepositoryInstallation:
    repository_id: int
    repository: str
    owner_id: int
    installation_id: int


@dataclass(frozen=True)
class RepositoryContext:
    repository_id: int
    repository: str
    owner_id: int
    installation_id: int
    generation: int
