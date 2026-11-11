let note = "source-artifact identity is not implemented until P6.5: this build carries no stamp, so the revision, checkout state and source-inventory digest are unknown and the library/executor artifact match is unverified"
let describe () =
  let module E = Hardcaml_asic_execution.Synthesis_envelope in
  let module J = Hardcaml_asic_execution.Identity_json in
  let active = E.requested (Array.to_list (Unix.environment ())) in
  let execution_envelope = if active then Yojson.Basic.from_string (J.canonical (E.to_json ())) else `Null in
  let module_dir=Flow_cli_paths.resources () in
  let data_dir=module_dir^"/data" in
  let version,source=try
    let ic=open_in (data_dir^"/version.txt") in
    let s=Fun.protect ~finally:(fun () -> close_in ic) (fun () -> String.trim (really_input_string ic (in_channel_length ic))) in
    if s="" then "0.1.0","source-tree-declaration" else s,"installed-package-metadata"
    with Sys_error _ | End_of_file -> "0.1.0","source-tree-declaration" in
  let strings xs = `List (List.map (fun s -> `String s) xs) in
  `Assoc ["tool",`String "hardcaml-asic-flow";"identity_schema",`Int 1;
    "package_version",`String version;"package_version_source",`String source;
    "supported_record_schemas",`List [`Int 1];
    "source",`Assoc ["stamped",`Bool false;"revision",`Null;"state",`String "unknown";
      "inventory_digest",`Null;"library_artifact_match",`Null;"note",`String note];
    "module_dir",`String module_dir;"data_dir",`String data_dir;
    "execution_envelope",execution_envelope;
    "toolchain_lock",`String (data_dir^"/toolchain.lock");
    "toolchain_lock_present",`Bool (try (Unix.stat (data_dir^"/toolchain.lock")).Unix.st_kind=Unix.S_REG with Unix.Unix_error _ -> false);
    "cli_contract",`Assoc ["version",`Int 4;"default_output",`String "human";"json_option",`String "--json";
      "results_defaults",`Assoc ["root",`String "<caller-cwd>/_flow_results";"operations",`String "<results-root>/operations";
        "runs",`String "<results-root>/runs";"stage_observations",`String "<resolved-workspace-parent>/observations"];
      "capabilities",`Assoc [
            "namedflow1",`Bool true;
            "synthesis_collection1",`Assoc ["schema_version",`Int 1;
              "profile",`String "synthesis-total-tasks-v1";"total_container_tasks",`Int 6;
              "fresh_only",`Bool true;"acceptance",`Bool false;
              "opt_in",`String "HARDCAML_ASIC_SYNTHESIS_TASK_LIMIT=6"];
           "workload_inspection1",`Assoc ["schema_version",`Int 1;"read_only",`Bool true;
             "max_response_bytes",`Int (1024*1024);"result_contract",`String "hardcaml-asic.workload-result"];
          "workload_comparison1",`Assoc ["schema_version",`Int 1;"read_only",`Bool true;
            "result_contract",`String "hardcaml-asic.workload-result";
            "options",strings ["left-workload";"right-workload"]];
         "variant_comparison1",`Assoc ["schema_version",`Int 1;"read_only",`Bool true;
           "max_response_bytes",`Int (1024*1024);"target_kinds",strings ["bundle";"coarse_run";"execution"];
           "axes",strings ["hardware";"resource_policy";"flow_configuration";"descriptive"]];
         "preparation1",`Bool true;
         "inspection1",`Bool true;
          "resource_evidence1",`Bool true;
           "resource_evidence2",`Bool true;
           "resource_inspection1",`Bool true;
          "resource_evidence_schemas",`Assoc ["write",`Int 2;"read",`List [`Int 1;`Int 2]];
          "timing_analysis1",`Bool true;
          "timing_intent_physical2",`Assoc ["kind",`String "coarse_run";"macro_free",`Bool true;
            "functional_postcheck",`String "cmos5l_physical_timing_used_cells_v1";
            "source_closure",`String "extracted_state_views_retained_filter_v2";
            "audit_schema",`Int 3;"companion_schema",`Int 2;"all_configured_synthesis",`Bool true;
            "full",`Bool true;"named_physical",`Bool false;"native_qualification",`String "pending";
            "workers",`Int 6;"sta_threads",`Int 1;"native_path_effect_coverage",`String "unavailable"];
         "timing_intent_evidence1",`Assoc ["read_only",`Bool true;"scope",`String "standalone_synthesized_netlist_sta";"native_path_effect_coverage",`String "unavailable"];
         "timing_intent_evidence2",`Assoc ["read_only",`Bool true;"profile",`String "opensta-2.7-native-state-v2";"native_data_schema",`Int 2;"native_path_effect_coverage",`String "unavailable"];
          "timing_intent_flow1",`Assoc ["kind",`String "coarse_run";"stage",`String "synthesized_netlist";"native_path_effect_coverage",`String "unavailable";"named_execution",`Bool false];
           "timing_intent_execution1",`Assoc ["kind",`String "execution";"target",`String "exact_execution_json";"stage",`String "synthesized_netlist";"native_path_effect_coverage",`String "unavailable";"named_archive",`Bool false;"relocation",`Bool false;
             "external_aliases",`Assoc ["contract",`String "declared_external_collateral_target_v1";"precheck_requires_roots",`Bool true;"counts_declared_paths",`Bool true];
             "outer_inventory",`Assoc ["max_sources",`Int 4096;"max_source_bytes",`Int (128*1024*1024);"max_total_bytes",`Int (512*1024*1024)]];
        "timing_stage1",`Bool true;
        "json_commands",strings ["preflight";"run";"postcheck";"collect";"report";"inspect";"execute";"archive";"stages";"checkpoint-inspect";
               "observation describe";"observation inspect";"observation read-updates";"observation watch";"finalize";"finalization-inspect";"preparation-inspect";"resources-inspect";"resource-inspect";"timing-intent-inspect";"compare";"workload-inspect"];
        "json_lines_commands",strings ["stages";"finalize";"observation watch"];
        "observation_watch",`Assoc ["incremental",`Bool true;"interval_seconds",`Float 0.5;
          "envelopes",strings ["updates";"status"];"records_flushed",`Bool true;
          "reconnect",`String "stream-id_and_after_together";
          "exit_codes",`Assoc ["terminal",`Int 0;"writer_lost_or_unknown",`Int 3;"query_error",`Int 2;"reader_interrupt",`Int 130]];
        "stage_observation_default",`Bool true;"stage_observation_schemas",`Assoc ["write",`Int 2;"read",`List [`Int 1;`Int 2]];
        "producer_event_timestamps",`Bool true;"monotonic_elapsed_seconds",`Bool true;
        "stage_observation_opt_out",`String "--no-observation";"stage_terminal_error_event",`String "observation_terminal";
        "stage_json_records_flushed",`Bool true;"json_flag_placements",strings ["global";"command";"observation-query"];
        "verbose_diagnostics",`String "stderr";"runs_override",`Bool true;"preflight_explicit_output",`String "file-existing-parent";
        "collect_json_writes_results",`Bool true;"collect_stdout",`String "legacy-read-only-json";
        "archive_json_list",`Bool false;"provision_json",`Bool false]]]
let report json =
  let j=describe () in
  if json then print_endline (Yojson.Basic.pretty_to_string j) else (
    let get k=match j with `Assoc xs -> (match List.assoc k xs with `String s -> s | _ -> "") | _ -> assert false in
    Printf.printf "hardcaml-asic-flow %s (hardcaml_asic package version, %s)\n" (get "package_version") (get "package_version_source");
    print_endline "source identity   unstamped; revision unknown, library artifact match unverified (P6.5)";
    print_endline "record schemas    1\nCLI contract      4; human output by default, --json for machine output";
    Printf.printf "modules           %s\nruntime data      %s\n" (get "module_dir") (get "data_dir"))
