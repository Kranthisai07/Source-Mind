INSERT INTO organizations (id, name, slug, plan)
VALUES (
    '81000000-0000-4000-8000-000000000001',
    'Ownerless Compatibility',
    'ownerless-compatibility',
    'free'
);

INSERT INTO users (id, clerk_id, email, display_name)
VALUES
    (
        '81000000-0000-4000-8000-000000000011',
        'ownerless-active-admin',
        'ownerless-active-admin@example.invalid',
        'Ownerless Active Admin'
    ),
    (
        '81000000-0000-4000-8000-000000000012',
        'ownerless-active-member',
        'ownerless-active-member@example.invalid',
        'Ownerless Active Member'
    ),
    (
        '81000000-0000-4000-8000-000000000013',
        'owner-backed-control',
        'owner-backed-control@example.invalid',
        'Owner-backed Control'
    ),
    (
        '81000000-0000-4000-8000-000000000014',
        'ownerless-departed-member',
        'ownerless-departed-member@example.invalid',
        'Ownerless Departed Member'
    ),
    (
        '81000000-0000-4000-8000-000000000015',
        'deleted-workspace-admin',
        'deleted-workspace-admin@example.invalid',
        'Deleted Workspace Admin'
    ),
    (
        '81000000-0000-4000-8000-000000000016',
        'ownerless-outsider',
        'ownerless-outsider@example.invalid',
        'Ownerless Outsider'
    );

INSERT INTO workspaces (
    id, organization_id, name, slug, deleted_at
)
VALUES
    (
        '82000000-0000-4000-8000-000000000001',
        '81000000-0000-4000-8000-000000000001',
        'Legacy Ownerless Workspace',
        'legacy-ownerless-workspace',
        NULL
    ),
    (
        '82000000-0000-4000-8000-000000000002',
        '81000000-0000-4000-8000-000000000001',
        'Owner-backed Control',
        'owner-backed-control',
        NULL
    ),
    (
        '82000000-0000-4000-8000-000000000003',
        '81000000-0000-4000-8000-000000000001',
        'Deleted Ownerless Workspace',
        'deleted-ownerless-workspace',
        NOW()
    );

INSERT INTO workspace_members (
    id, workspace_id, user_id, role, status, departed_at
)
VALUES
    (
        '83000000-0000-4000-8000-000000000001',
        '82000000-0000-4000-8000-000000000001',
        '81000000-0000-4000-8000-000000000011',
        'admin',
        'active',
        NULL
    ),
    (
        '83000000-0000-4000-8000-000000000002',
        '82000000-0000-4000-8000-000000000001',
        '81000000-0000-4000-8000-000000000012',
        'member',
        'active',
        NULL
    ),
    (
        '83000000-0000-4000-8000-000000000003',
        '82000000-0000-4000-8000-000000000002',
        '81000000-0000-4000-8000-000000000013',
        'owner',
        'active',
        NULL
    ),
    (
        '83000000-0000-4000-8000-000000000004',
        '82000000-0000-4000-8000-000000000001',
        '81000000-0000-4000-8000-000000000014',
        'member',
        'departed',
        NOW()
    ),
    (
        '83000000-0000-4000-8000-000000000005',
        '82000000-0000-4000-8000-000000000003',
        '81000000-0000-4000-8000-000000000015',
        'admin',
        'active',
        NULL
    );

INSERT INTO documents (
    id, workspace_id, submitter_id, title, source_type,
    sha256_hash, ingestion_status
)
VALUES
    (
        '84000000-0000-4000-8000-000000000001',
        '82000000-0000-4000-8000-000000000001',
        '81000000-0000-4000-8000-000000000011',
        'Ownerless Compatibility Data',
        'text',
        repeat('a', 64),
        'complete'
    ),
    (
        '84000000-0000-4000-8000-000000000002',
        '82000000-0000-4000-8000-000000000002',
        '81000000-0000-4000-8000-000000000013',
        'Owner-backed Control Data',
        'text',
        repeat('b', 64),
        'complete'
    ),
    (
        '84000000-0000-4000-8000-000000000003',
        '82000000-0000-4000-8000-000000000003',
        '81000000-0000-4000-8000-000000000015',
        'Deleted Workspace Data',
        'text',
        repeat('c', 64),
        'complete'
    );
