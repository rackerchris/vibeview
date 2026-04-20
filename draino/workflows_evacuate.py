"""Full evacuation workflow implementation."""
from __future__ import annotations

from typing import Callable, Optional

from .models import InstanceInfo, NodePhase, NodeState, StepStatus
from . import workflows_maintenance

UpdateFn = Callable[[], None]
LogFn = Callable[[str], None]
AuditCb = Callable[[str, str], None]

POLL_INTERVAL = 15
MIGRATE_TIMEOUT = 1800
EMPTY_TIMEOUT = 900


def _remaining_instance_names(openstack_ops, hypervisor: str, auth=None) -> list[str]:
    try:
        items = openstack_ops.list_servers_on_host(hypervisor, lambda _msg: None, auth=auth)
    except Exception:
        return []
    names: list[str] = []
    for item in items:
        name = str(item.get("name") or item.get("id") or "").strip()
        if name:
            names.append(name)
    return names


def run_workflow(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    time_module,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    ctx = workflows_maintenance.WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Evacuation aborted",
    )

    state.phase = NodePhase.RUNNING
    state.instances = []
    state.nova_evacuation_status = "in_progress"
    state.nova_evacuation_detail = "disabling nova and evacuating instances"
    state.init_steps()
    update_cb()

    ctx.step_set("disable_nova", StepStatus.RUNNING)
    try:
        openstack_ops.disable_compute_service(state.hypervisor, ctx.log, auth=openstack_auth)
        state.compute_status = "disabled"
        state.nova_evacuation_detail = "nova disabled; enumerating instances on host"
        ctx.step_set("disable_nova", StepStatus.SUCCESS)
    except Exception as exc:
        state.nova_evacuation_status = "error"
        state.nova_evacuation_detail = str(exc)
        ctx.abort("disable_nova", str(exc))
        return

    ctx.step_set("list_instances", StepStatus.RUNNING)
    try:
        servers = openstack_ops.list_servers_on_host(state.hypervisor, ctx.log, auth=openstack_auth)
        amp_map = openstack_ops.get_amphora_lb_mapping(ctx.log, auth=openstack_auth)
        for s in servers:
            lb_id = amp_map.get(s["id"])
            is_amp = s["is_amphora"] or lb_id is not None
            state.instances.append(
                InstanceInfo(
                    id=s["id"],
                    name=s["name"],
                    status=s["status"],
                    is_amphora=is_amp,
                    lb_id=lb_id,
                )
            )
        n_vm = sum(1 for i in state.instances if not i.is_amphora)
        n_amp = sum(1 for i in state.instances if i.is_amphora)
        state.vm_count = n_vm
        state.amphora_count = n_amp
        state.nova_evacuation_detail = f"nova disabled; {n_vm} VM(s) and {n_amp} Amphora remain on host"
        ctx.step_set("list_instances", StepStatus.SUCCESS, f"{n_vm} VM(s), {n_amp} Amphora")
    except Exception as exc:
        state.nova_evacuation_status = "error"
        state.nova_evacuation_detail = str(exc)
        ctx.abort("list_instances", str(exc))
        return

    ctx.step_set("migrate_vms", StepStatus.RUNNING)
    vms = [i for i in state.instances if not i.is_amphora]

    if not vms:
        ctx.step_set("migrate_vms", StepStatus.SUCCESS, "No VMs to migrate")
    else:
        for inst in vms:
            inst.migration_status = "queued"
            update_cb()
            try:
                openstack_ops.live_migrate_server(inst.id, ctx.log, auth=openstack_auth)
                inst.migration_status = "migrating"
            except Exception as live_exc:
                task_state = openstack_ops.get_server_task_state(inst.id, auth=openstack_auth) or ""
                if "migrat" in task_state.lower():
                    ctx.log(
                        f"Live migration for '{inst.name}' timed out but instance "
                        f"is already in task_state '{task_state}' — continuing to poll"
                    )
                    inst.migration_status = "migrating"
                else:
                    ctx.log(f"Live migration failed for '{inst.name}': {live_exc} — trying cold migration")
                    try:
                        openstack_ops.cold_migrate_server(inst.id, ctx.log, auth=openstack_auth)
                        inst.migration_status = "cold-migrating"
                    except Exception as cold_exc:
                        ctx.log(f"Cold migration also failed for '{inst.name}': {cold_exc}")
                        inst.migration_status = "failed"
            update_cb()

        deadline = time_module.time() + MIGRATE_TIMEOUT
        while time_module.time() < deadline:
            pending = [i for i in vms if i.migration_status not in ("complete", "failed")]
            if not pending:
                break

            for inst in pending:
                nova_status = openstack_ops.get_server_status(inst.id, auth=openstack_auth)

                if nova_status == "VERIFY_RESIZE":
                    inst.migration_status = "confirming"
                    update_cb()
                    try:
                        openstack_ops.confirm_resize_server(inst.id, ctx.log, auth=openstack_auth)
                    except Exception as exc:
                        ctx.log(f"Confirm resize failed for '{inst.name}': {exc}")
                        inst.migration_status = "failed"
                elif nova_status == "ACTIVE":
                    migs = openstack_ops.get_server_migrations(inst.id, auth=openstack_auth)
                    if migs:
                        latest = migs[0]["status"].lower()
                        if latest in ("completed", "done", "finished"):
                            inst.migration_status = "complete"
                        elif latest in ("error", "failed"):
                            inst.migration_status = "failed"
                            ctx.log(f"Migration failed for '{inst.name}'")
                    else:
                        inst.migration_status = "complete"
                elif nova_status == "ERROR":
                    inst.migration_status = "failed"
                    ctx.log(f"Server '{inst.name}' entered ERROR state")

            done_n = sum(1 for i in vms if i.migration_status == "complete")
            failed_n = sum(1 for i in vms if i.migration_status == "failed")
            state.nova_evacuation_detail = f"nova disabled; migrating instances ({done_n}/{len(vms)} complete, {failed_n} failed)"
            step = state.get_step("migrate_vms")
            if step:
                step.detail = f"{done_n}/{len(vms)} done, {failed_n} failed"
            update_cb()
            time_module.sleep(POLL_INTERVAL)

        failed_n = sum(1 for i in vms if i.migration_status == "failed")
        done_n = sum(1 for i in vms if i.migration_status == "complete")
        if failed_n:
            failed_names = [i.name for i in vms if i.migration_status == "failed" and i.name]
            names_suffix = f" ({', '.join(failed_names[:4])}{' +more' if len(failed_names) > 4 else ''})" if failed_names else ""
            detail = f"{done_n}/{len(vms)} migrated — {failed_n} failed{names_suffix}"
            state.nova_evacuation_status = "blocked"
            state.nova_evacuation_detail = f"nova disabled but not evacuated; migration failures: {', '.join(failed_names[:4])}" if failed_names else detail
            ctx.abort("migrate_vms", detail)
            return
        ctx.step_set("migrate_vms", StepStatus.SUCCESS, f"{done_n}/{len(vms)} migrated")
        state.nova_evacuation_detail = "nova disabled; regular VM migrations complete"

    ctx.step_set("failover_lbs", StepStatus.RUNNING)
    amphora = [i for i in state.instances if i.is_amphora and i.lb_id]

    if not amphora:
        ctx.step_set("failover_lbs", StepStatus.SUCCESS, "No Amphora to fail over")
    else:
        lb_ids = list({i.lb_id for i in amphora})
        any_failed = False

        for lb_id in lb_ids:
            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = "failing_over"
            update_cb()

            try:
                openstack_ops.failover_loadbalancer(lb_id, ctx.log, auth=openstack_auth)
                ok = openstack_ops.wait_for_lb_active(lb_id, ctx.log, auth=openstack_auth)
                fo_status = "complete" if ok else "failed"
                if not ok:
                    any_failed = True
                    ctx.log(f"LB {lb_id} did not return to ACTIVE")
            except Exception as exc:
                ctx.log(f"Failover error for LB {lb_id}: {exc}")
                fo_status = "failed"
                any_failed = True

            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = fo_status
            update_cb()

        if any_failed:
            state.nova_evacuation_status = "blocked"
            failed_lbs = sorted({inst.lb_id for inst in amphora if inst.failover_status == "failed" and inst.lb_id})
            state.nova_evacuation_detail = (
                f"nova disabled but not evacuated; Amphora failover failed for {', '.join(failed_lbs[:4])}"
                if failed_lbs else
                "nova disabled but not evacuated; one or more load balancer failovers failed"
            )
            ctx.abort("failover_lbs", "One or more LB failovers failed")
            return
        ctx.step_set("failover_lbs", StepStatus.SUCCESS, f"{len(lb_ids)} LB(s) failed over")
        state.nova_evacuation_detail = "nova disabled; Amphora failover complete, waiting for host to empty"

    ctx.step_set("await_empty", StepStatus.RUNNING)
    deadline = time_module.time() + EMPTY_TIMEOUT
    count = -1
    while time_module.time() < deadline:
        try:
            count = openstack_ops.count_servers_on_host(state.hypervisor, auth=openstack_auth)
        except Exception:
            count = -1
        step = state.get_step("await_empty")
        if step:
            step.detail = f"{count} instance(s) remaining" if count >= 0 else "checking…"
        if count >= 0:
            state.vm_count = max(0, count - (state.amphora_count or 0))
            state.amphora_count = state.amphora_count
            state.nova_evacuation_detail = f"nova disabled; {count} instance(s) still remain on host"
        update_cb()
        if count == 0:
            state.vm_count = 0
            state.amphora_count = 0
            break
        time_module.sleep(POLL_INTERVAL)
    else:
        remaining_names = _remaining_instance_names(openstack_ops, state.hypervisor, auth=openstack_auth)
        remaining_suffix = f" ({', '.join(remaining_names[:5])}{' +more' if len(remaining_names) > 5 else ''})" if remaining_names else ""
        state.nova_evacuation_status = "blocked"
        state.nova_evacuation_detail = f"nova disabled but not evacuated; {count} instance(s) remain{remaining_suffix}"
        ctx.abort("await_empty", f"Timeout — {count} instance(s) still present{remaining_suffix}")
        return

    ctx.step_set("await_empty", StepStatus.SUCCESS, "Hypervisor empty")
    state.nova_evacuation_status = "evacuated"
    state.nova_evacuation_detail = "compute service disabled and hypervisor empty"

    state.phase = NodePhase.COMPLETE
    ctx.log(f"✓ '{state.k8s_name}' evacuated — nova disabled, hypervisor empty")
    if audit_cb:
        audit_cb("completed", "nova disabled, hypervisor empty")
    update_cb()
