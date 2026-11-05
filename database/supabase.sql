-- Run once in the project's SQL Editor. No key or password belongs in this file.
begin;
create table if not exists public.opc_workspaces (
    id uuid primary key,
    revision bigint not null default 0,
    payload jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);
create table if not exists public.opc_ai_usage (
    day date primary key,
    used integer not null default 0 check (used between 0 and 100)
);
alter table public.opc_workspaces enable row level security;
alter table public.opc_ai_usage enable row level security;
revoke all on public.opc_workspaces, public.opc_ai_usage from public, anon, authenticated;

create or replace function public.opc_workspace_save(p_id uuid,p_payload jsonb,p_expected_revision bigint)
returns bigint language plpgsql security definer set search_path = pg_catalog, public as $$
declare current_revision bigint;
begin
    if p_expected_revision is null or p_expected_revision < 0 or p_payload->>'schema' is distinct from '2' then
        raise exception 'OPC_INVALID_PAYLOAD';
    end if;
    insert into public.opc_workspaces(id) values(p_id) on conflict do nothing;
    select revision into current_revision from public.opc_workspaces where id=p_id for update;
    if current_revision <> p_expected_revision then raise exception 'OPC_REVISION_CONFLICT'; end if;
    update public.opc_workspaces set revision=current_revision+1,
        payload=p_payload || jsonb_build_object('revision',current_revision+1),updated_at=now() where id=p_id;
    return current_revision+1;
end $$;

create or replace function public.opc_workspace_read(p_id uuid)
returns jsonb language plpgsql security definer set search_path = pg_catalog, public as $$
declare result jsonb;
begin
    select payload into result from public.opc_workspaces where id=p_id;
    if result is null then raise exception 'OPC_NOT_FOUND'; end if;
    return result;
end $$;

create or replace function public.opc_quota_status()
returns jsonb language sql security definer set search_path = pg_catalog, public as $$
    select jsonb_build_object('used',coalesce((select used from public.opc_ai_usage
        where day=(now() at time zone 'Asia/Shanghai')::date),0));
$$;

create or replace function public.opc_quota_reserve()
returns boolean language plpgsql security definer set search_path = pg_catalog, public as $$
declare today date := (now() at time zone 'Asia/Shanghai')::date;
declare changed integer;
begin
    insert into public.opc_ai_usage(day,used) values(today,0) on conflict do nothing;
    update public.opc_ai_usage set used=used+1 where day=today and used<100;
    get diagnostics changed = row_count;
    return changed=1;
end $$;

create or replace function public.opc_quota_floor(p_day date,p_used integer)
returns integer language plpgsql security definer set search_path = pg_catalog, public as $$
declare current_day date := (now() at time zone 'Asia/Shanghai')::date;
declare result integer;
begin
    if p_day is distinct from current_day or p_used is null or p_used < 0 or p_used > 100 then
        raise exception 'OPC_INVALID_QUOTA_FLOOR';
    end if;
    insert into public.opc_ai_usage(day,used) values(current_day,p_used)
        on conflict(day) do update set used=greatest(public.opc_ai_usage.used,excluded.used)
        returning used into result;
    return result;
end $$;

revoke all on function public.opc_workspace_save(uuid,jsonb,bigint) from public,anon,authenticated;
revoke all on function public.opc_workspace_read(uuid) from public,anon,authenticated;
revoke all on function public.opc_quota_status() from public,anon,authenticated;
revoke all on function public.opc_quota_reserve() from public,anon,authenticated;
grant execute on function public.opc_workspace_save(uuid,jsonb,bigint) to service_role;
grant execute on function public.opc_workspace_read(uuid) to service_role;
grant execute on function public.opc_quota_status() to service_role;
grant execute on function public.opc_quota_reserve() to service_role;
revoke all on function public.opc_quota_floor(date,integer) from public,anon,authenticated;
grant execute on function public.opc_quota_floor(date,integer) to service_role;
notify pgrst, 'reload schema';
commit;
