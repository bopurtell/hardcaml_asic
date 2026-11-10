open Hardcaml_asic_execution
let () =
  let left=ref "" and right=ref "" and left_kind=ref "" and right_kind=ref "" and axis=ref "" and json=ref false in
  let checked = function Ok x -> x | Error e -> invalid_arg e in
  let left_workload=ref "" and right_workload=ref "" in
  let seen=Hashtbl.create 5 in
  let once key value = Arg.String (fun s ->
    if Hashtbl.mem seen key then invalid_arg ("duplicate --"^key);
    Hashtbl.add seen key ();value:=s) in
  try
    Arg.parse ["--left",once "left" left,"Left target";"--right",once "right" right,"Right target";
      "--left-kind",once "left-kind" left_kind,"bundle/coarse_run/execution";
      "--right-kind",once "right-kind" right_kind,"bundle/coarse_run/execution";
      "--left-workload",once "left-workload" left_workload,"Explicit left workload result";
      "--right-workload",once "right-workload" right_workload,"Explicit right workload result";
      "--axis",once "axis" axis,"hardware/resource_policy/flow_configuration/descriptive";
      "--json",Arg.Set json,"JSON";"--verbose",Arg.Unit (fun () -> ()),"Verbose"]
      (fun _ -> invalid_arg "compare requires two explicit named target references") "Read-only variant comparison";
    let left=checked (Variant_comparison.target ~kind:!left_kind ~path:!left)
    and right=checked (Variant_comparison.target ~kind:!right_kind ~path:!right) in
    let optional key value = if Hashtbl.mem seen key then (
      if String.trim !value="" then invalid_arg ("empty --"^key);Some !value) else None in
    let result=checked (Variant_comparison.inspect ~left ~right ~axis:(checked (Variant_comparison.axis !axis))
      ?left_workload:(optional "left-workload" left_workload)
      ?right_workload:(optional "right-workload" right_workload) ()) in
    if !json then print_endline (Evidence_json.record_bytes result) else (
      let get k=match result with `Assoc xs -> List.assoc k xs | _ -> assert false in
      let show = function `String x -> x | x -> Evidence_json.record_bytes x in
      Printf.printf "VARIANT COMPARISON  %s\nAxis: %s\n" (show (get "comparison_digest")) (show (get "axis"));
      let metrics=match get "metrics" with `List xs -> xs | _ -> [] in
      List.iter (fun state ->
        let n=List.length (List.filter (function `Assoc xs -> List.assoc_opt "comparison" xs=Some (`String state) | _ -> false) metrics) in
        Printf.printf "  %s: %d metrics\n" state n) ["comparable";"confounded";"unavailable"];
      print_endline "Delta direction: right minus left. No overall selection verdict.\nUse --json for exact identities, controls, outcomes and reasons.")
  with Invalid_argument e | Failure e -> prerr_endline ("compare: "^e);exit 2
