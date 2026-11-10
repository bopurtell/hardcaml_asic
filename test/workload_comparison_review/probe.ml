open Hardcaml_asic_execution
let () =
  let emit j = print_endline (Evidence_json.record_bytes j) in
  let run = function Ok j -> emit j | Error e -> prerr_endline e;exit 2 in
  let target kind path = match Variant_comparison.target ~kind ~path with
    | Ok x -> x | Error e -> invalid_arg e in
  match Array.to_list Sys.argv with
  | [_;"metric";left;right] -> emit (Variant_comparison.compare_metric ~axis:Hardware
      ~left:(Yojson.Safe.from_string left) ~right:(Yojson.Safe.from_string right))
  | [_;"workload";path] -> run (Workload_result.inspect path)
  | [_;lk;left;rk;right;lw;rw] -> run (Variant_comparison.inspect
      ~left:(target lk left) ~right:(target rk right) ~axis:Resource_policy
      ~left_workload:lw ~right_workload:rw ())
  | _ -> exit 2
