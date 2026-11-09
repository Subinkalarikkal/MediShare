# Service Documentation

All services live in `services/` (plus AI services in `ai/`). Signature convention:

```python
result = service.function(session, actor, *args, **kwargs)
```

* `session` – SQLAlchemy session (caller commits; pages use `components.session.act()` which commits/rolls back).
* `actor` – `permission_service.Actor` identifying the caller. Its roles are **not trusted**; each function calls
  `require(...)` which reloads them from the database.
* Errors: `ValidationError` (field list in `.errors`), `PermissionDenied`, `NotFound`, `WorkflowError`,
  `SafetyViolation` (a `WorkflowError` for mandatory safety rules).

| Service | Key functions | Permission |
|---|---|---|
| `auth_service` | `resolve_login`, `register_user`, `record_logout`, `is_session_expired`, `bootstrap_super_admin`, `hash_password` / `verify_password` (Argon2id) | – |
| `permission_service` | `load_actor`, `require`, `require_any`, `require_role`, `require_org_member`, `require_verified_professional`, `active_authorizations` | – |
| `user_service` | `get_profile`, `update_profile`, `search_users`, `suspend_user`, `reactivate_user`, `verify_professional`, `assign_role`, `revoke_role`, `activity_history`, `view_user` | `users.manage` (admin roles need `admins.manage`) |
| `organization_service` | `register_organization`, `update_organization`, `upload_document`, `add_authorization`, `submit_for_verification`, `decide_verification`, `decide_authorization`, `documentation_gaps`, `read_document`, `list_organizations`, `public_verified_organizations` | `org.manage_own` / `org.verify` |
| `medicine_service` | master: `search_medicines`, `create_medicine`, `update_medicine`; household: `create_registration`, `update_draft`, `submit_registration`, `cancel_registration`, `delete_draft`, `list_my_registrations`, `get_registration`, `admin_list_registrations`, `admin_set_status` | `medicine.register` / `medicine_master.manage` / `monitor.all` |
| `inventory_service` | `create_batch`, `update_draft`, `attach_document`, `submission_blockers`, `submit_batch`, `withdraw_batch`, `close_batch`, `list_org_inventory`, `get_batch`, `admin_list_inventory`, `quarantine_expired` | `inventory.manage` |
| `collection_service` | centres: `register_centre`, `update_centre`, `set_operational_status`, `decide_centre`, `search_centres` (public), `is_authorized_centre`; requests: `create_request`, `cancel_request`, `accept_request`, `reject_request`, `schedule_request`, `confirm_receipt`, `initiate_disposal`, `close_request`, `list_my_requests`, `centre_requests`, `request_events`, `delayed_requests` | `collection.request.create` / `centre.operate` / `centre.verify` |
| `disposal_service` | `record_disposal`, `centre_disposal_records`, `admin_disposal_records`, `trace_registration` | `disposal.record` |
| `review_service` | `review_queue`, `batch_details`, `record_decision`, `review_interest`, `pending_interests`, `my_review_history`, `org_review_history` | `review.inventory` + verified professional |
| `institutional_request_service` | `create_request`, `submit_request`, `cancel_request`, `recommendations` (matching engine), `express_interest`, `request_items`, `accepted_interests` | `institutional_request.manage` + verified org |
| `transfer_service` | `create_transfer`, `add_authorization`, `missing_authorizations`, `mark_authorized`, `mark_completed`, `cancel_transfer`, `list_transfers` | `transfer.document` / `transfer.authorize_quality` |
| `notification_service` | `notify`, `notify_role`, `notify_organization`, `list_my_notifications`, `mark_read`, `mark_all_read`, `broadcast`, `submit_contact_message` | own data / `notifications.manage` |
| `analytics_service` | `public_stats`, `admin_kpis`, monthly/district/category/risk/centre/pending/institutional series, `quantities_by_unit`, `incomplete_documentation`, role overviews, `assistant_snapshot` | `analytics.view` |
| `settings_service` | `list_settings`, `update_setting` (validated), `role_permission_matrix`, `set_role_permission` (safety-guarded), `audit_logs`, `integration_status` | super-admin permissions / `audit.view` |
| `storage_service` | `store_upload` (validated, private, SHA-256), `read_file`, `signed_url` (S3 presigned) | called by services |
| `ai.medicine_extraction` | `extract_from_image`, `validate_model_output`, `extract_from_ocr_text` | `medicine.register` |
| `ai.expiry_risk_engine` | `compute`, `run_assessment`, `latest_assessments`, `open_alerts`, `acknowledge_alert` | `risk.run` / `monitor.all` |
| `ai.analytics_assistant` | `ask`, `validate_response` | `ai.assistant` + `analytics.view` |

## State machines
Defined in `utils/constants.py`: `REGISTRATION_TRANSITIONS`, `INVENTORY_TRANSITIONS`, `COLLECTION_TRANSITIONS`,
`VERIFICATION_TRANSITIONS`. Any other transition raises `WorkflowError`.

## Matching engine (v1.0.0)
Candidates: batches `APPROVED_FOR_FURTHER_AUTHORIZED_PROCESS`, other organizations only, recall checked, ≥ 90
days shelf life, not restricted, donor holds an in-date licence. Score (0–100): product identity 40 / name 25 /
category 10, quantity coverage ≤ 20, expiry headroom ≤ 20, storage documented 10, same district 10. Output is a
recommendation list for authorized review only.
