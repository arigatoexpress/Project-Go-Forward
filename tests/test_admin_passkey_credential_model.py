"""Canonical Firestore schema for staff passkey credentials."""

from datetime import UTC, datetime

from auth.store import CredentialRecord, FirestoreCredentialStore
from database.models import AdminPasskeyCredential


def test_passkey_credential_round_trips_label():
    created = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
    used = datetime(2026, 10, 24, 19, 0, tzinfo=UTC)
    document = AdminPasskeyCredential(
        credential_id="Y3JlZGVudGlhbC0y",
        public_key="cHVibGlj",
        sign_count=4,
        user_id="mark@texashomeoutlet.com",
        aaguid="fbfc3007-154e-4ecc-8c0a-6ef5e7a3e3c4",
        created_at=created,
        last_used_at=used,
        label="Front desk iPhone",
    )

    stored = document.model_dump()
    loaded = AdminPasskeyCredential.model_validate(stored)

    assert stored["label"] == "Front desk iPhone"
    assert loaded.label == "Front desk iPhone"
    assert loaded.created_at == created
    assert loaded.last_used_at == used
    assert loaded.model_dump() == stored


def test_passkey_credential_without_label_still_loads():
    created = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    legacy = {
        "credential_id": "b2xkLWtleQ",
        "public_key": "b2xkLXB1YmxpYw",
        "sign_count": 2,
        "user_id": "owner@texashomeoutlet.com",
        "aaguid": "",
        "created_at": created,
        "last_used_at": None,
        "unrelated_legacy_field": "kept out of the schema",
    }

    loaded = AdminPasskeyCredential.from_document("b2xkLWtleQ", legacy)

    assert loaded.label == ""
    assert loaded.credential_id == "b2xkLWtleQ"
    assert loaded.user_id == "owner@texashomeoutlet.com"
    assert loaded.sign_count == 2
    assert loaded.created_at == created
    assert "unrelated_legacy_field" not in loaded.model_dump()


def test_passkey_credential_null_label_loads_blank():
    loaded = AdminPasskeyCredential.from_document(
        "b2xkLWtleQ",
        {
            "credential_id": "b2xkLWtleQ",
            "public_key": "b2xkLXB1YmxpYw",
            "user_id": "owner@texashomeoutlet.com",
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "label": None,
        },
    )

    assert loaded.label == ""


def test_store_document_uses_the_canonical_model_for_label():
    created = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
    record = CredentialRecord(
        credential_id=b"credential-2",
        public_key=b"public",
        sign_count=4,
        user_id="mark@texashomeoutlet.com",
        aaguid="",
        created_at=created,
        label="Front desk iPhone",
    )

    stored = FirestoreCredentialStore._document_body(record)
    loaded = AdminPasskeyCredential.model_validate(stored)
    round_trip = FirestoreCredentialStore._to_record(stored["credential_id"], stored)

    assert loaded.label == "Front desk iPhone"
    assert round_trip.label == "Front desk iPhone"
    assert round_trip.public_key == b"public"
    assert round_trip.credential_id == b"credential-2"

    unlabeled = dict(stored)
    unlabeled.pop("label")
    legacy = FirestoreCredentialStore._to_record(stored["credential_id"], unlabeled)
    assert legacy.label == ""
    assert legacy.credential_id == b"credential-2"
    assert legacy.public_key == b"public"
