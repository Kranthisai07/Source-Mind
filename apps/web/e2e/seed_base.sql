-- Organization, identity, workspace and membership.
--
-- Separate from seed_conflicts.sql because these outlive a run, while the
-- conflict fixtures are wiped and recreated every time. Run once, after
-- migrating and before seeding conflicts.

INSERT INTO organizations (id, name, slug)
VALUES ('00000000-0000-4000-8000-0000000000f0', 'E2E Org', 'e2e-org');

-- The identity AUTH_DEV_BYPASS_ENABLED pins, in
-- apps/api/sourcemind/core/dependencies.py. The UUID is not arbitrary: the
-- bypass constructs an AuthenticatedUser with exactly this id, so the row must
-- exist or every workspace-scoped request fails its foreign key.
INSERT INTO users (id, clerk_id, email, display_name)
VALUES ('00000000-0000-4000-8000-000000000001', 'dev_user_1',
        'dev@sourcemind.local', 'Dev User');

INSERT INTO workspaces (id, organization_id, name, slug, created_by_user_id)
VALUES ('00000000-0000-4000-8000-0000000000a0',
        '00000000-0000-4000-8000-0000000000f0',
        'E2E Workspace', 'e2e-workspace',
        '00000000-0000-4000-8000-000000000001');

-- status='active' matters: the membership gate treats anything else as a
-- non-member and answers 404, which would turn the whole suite into
-- "conflict could not be loaded".
INSERT INTO workspace_members (workspace_id, user_id, role, status)
VALUES ('00000000-0000-4000-8000-0000000000a0',
        '00000000-0000-4000-8000-000000000001', 'owner', 'active');
