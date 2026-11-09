from dataclasses import replace

import pytest

from services import (
    collection_service, inventory_service, settings_service, user_service,
)
from services.errors import PermissionDenied
from services.permission_service import Actor
from tests.conftest import make_org, ready_batch, reg_data


def test_individual_cannot_access_admin_data(session, individual):
    with pytest.raises(PermissionDenied):
        user_service.search_users(session, individual)


def test_forged_actor_roles_are_ignored(session, individual):
    """Roles held in session state / browser are never trusted – permissions come from the DB."""
    forged = replace(individual, roles=frozenset({"SUPER_ADMIN"}), permissions=frozenset({"users.manage"}))
    with pytest.raises(PermissionDenied):
        user_service.search_users(session, forged)


def test_org_cannot_access_other_orgs_inventory(session, admin, donor, medicine):
    donor_actor, _ = donor
    other_actor, _ = make_org(session, admin, "other@test.in", "INSTITUTIONAL_DONOR", "PHARMACY",
                              auth_type="DRUG_LICENCE_RETAIL")
    b = ready_batch(session, donor_actor, medicine)
    with pytest.raises(PermissionDenied):
        inventory_service.get_batch(session, other_actor, b.id)
    assert inventory_service.list_org_inventory(session, other_actor) == []


def test_centre_cannot_access_unrelated_requests(session, admin, centre, individual):
    from services import medicine_service
    _, _, c = centre
    other_actor, _ = make_org(session, admin, "centre2@test.in", "COLLECTION_CENTRE", "COLLECTION_CENTRE",
                              auth_type="BIOMEDICAL_WASTE_AUTHORIZATION")
    reg = medicine_service.create_registration(session, individual, **reg_data())
    req = collection_service.create_request(session, individual, centre_id=c.id, registration_ids=[reg.id],
                                            collection_method="DROP_OFF", preferred_date=None)
    with pytest.raises(PermissionDenied):
        collection_service.accept_request(session, other_actor, req.id)
    with pytest.raises(PermissionDenied):
        collection_service.get_request(session, other_actor, req.id)


def test_reviewer_cannot_modify_system_configuration(session, reviewer):
    with pytest.raises(PermissionDenied):
        settings_service.update_setting(session, reviewer, "risk.quantity_reference", 10)


def test_admin_cannot_modify_settings_but_super_admin_can(session, admin, super_admin):
    with pytest.raises(PermissionDenied):
        settings_service.update_setting(session, admin, "risk.quantity_reference", 10)
    s = settings_service.update_setting(session, super_admin, "risk.quantity_reference", 500)
    assert s.value == 500


def test_suspended_org_cannot_submit_new_inventory(session, admin, donor, medicine):
    from services import organization_service
    donor_actor, org = donor
    organization_service.decide_verification(session, admin, org.id, "SUSPENDED", "Licence under inquiry")
    with pytest.raises(PermissionDenied):
        ready_batch(session, donor_actor, medicine)


def test_unknown_actor_denied(session):
    import uuid
    from services.errors import NotFound
    with pytest.raises(NotFound):
        user_service.get_profile(session, Actor(user_id=uuid.uuid4()))


def test_safety_separations_cannot_be_granted(session, super_admin):
    with pytest.raises(PermissionDenied):
        settings_service.set_role_permission(session, super_admin, "INDIVIDUAL", "inventory.manage", True)
    with pytest.raises(PermissionDenied):
        settings_service.set_role_permission(session, super_admin, "SUPER_ADMIN", "settings.manage", False)
