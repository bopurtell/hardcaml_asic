open Hardcaml_asic_execution
let () =
  let emit j = print_endline (Evidence_json.record_bytes j) in
  match Array.to_list Sys.argv with
  | [_;"report";path] -> emit (Report_service.run ~render_human:false ~strict_sources:true path).results
  | [_;"targets";left_kind;left;right_kind;right;lw;rw] ->
    let checked = function Ok x -> x | Error e -> prerr_endline e;exit 2 in
    let optional = function "-" -> None | p -> Some p in
    let left=checked (Variant_comparison.target ~kind:left_kind ~path:left)
    and right=checked (Variant_comparison.target ~kind:right_kind ~path:right) in
    (match Variant_comparison.inspect ~left ~right ~axis:Resource_policy
      ?left_workload:(optional lw) ?right_workload:(optional rw) () with
     | Ok j -> emit j | Error e -> prerr_endline e;exit 2)
  | [_;"metric";left;right] ->
    emit (Variant_comparison.compare_metric ~axis:Hardware
      ~left:(Yojson.Safe.from_string left) ~right:(Yojson.Safe.from_string right))
  | [_;left;right;lw;rw] ->
    let optional = function "-" -> None | p -> Some p in
    (match Variant_comparison.inspect ~left:(Bundle left) ~right:(Bundle right)
      ~axis:Resource_policy ?left_workload:(optional lw) ?right_workload:(optional rw) () with
     | Ok j -> emit j | Error e -> prerr_endline e;exit 2)
  | _ -> exit 2
