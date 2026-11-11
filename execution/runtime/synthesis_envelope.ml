(* Opt-in execution policy, separate from the existing executor/quota budget. *)
let variable = "HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT"
let requested environment =
  match List.filter (fun entry -> entry = variable || String.starts_with ~prefix:(variable ^ "=") entry) environment with
  | [] -> false
  | [entry] when entry = variable ^ "=6" -> true
  | _ -> invalid_arg (variable ^ " must occur exactly once with value 6, or be absent")
let pids_limit environment = if requested environment then Some 6 else None
let check_plan environment plan =
  if requested environment then match plan with
  | Execution_plan.Stages p when Execution_plan.Stage_plan.sequence p = [Execution_plan.Stage.Synthesis]
      && Execution_plan.Stage_plan.source_boundary p = None -> ()
  | _ -> invalid_arg "strict synthesis envelope requires one fresh synthesis-only stage recipe"
let check_boundary environment boundary =
  if requested environment && boundary <> Execution_plan.Stage.Synthesis then
    invalid_arg "strict synthesis envelope forbids physical/timing boundaries"
let to_json () =
  let module J = Identity_json in
  let ok = function Ok x -> x | Error _ -> assert false in
  ok (J.object_ ["schema_version",ok (J.integer "1");
    "profile",ok (J.string "synthesis-total-tasks-v1");
    "total_container_tasks",ok (J.integer "6");
    "configured_executor_max_workers",ok (J.integer "6");
    "reduced_usable_executor_concurrency",J.bool true])
