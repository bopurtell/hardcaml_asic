open Hardcaml_asic_execution
let () =
  let emit j = print_endline (Evidence_json.record_bytes j) in
  let run = function Ok j -> emit j | Error e -> prerr_endline e;exit 2 in
  match Array.to_list Sys.argv with
  | [_;"variant"] ->
    let module H=Hardcaml_asic.Hardware_variant in
    let ok=Core.Or_error.ok_exn in
    let parameter=ok (H.Parameter.create ~key:"depth" ~kind:(Integer {minimum=256;maximum=256})) in
    let schema=ok (H.Schema.create ~design_id:"workload-test" ~schema_id:"memory.v1" ~parameters:[parameter]) in
    let v=ok (H.create ~schema ~assignments:["depth",H.Value.Int 256]) in
    emit (H.to_yojson v)
  | [_;path] -> run (Workload_result.inspect path)
  | [_;path;expected_digest] ->
    (match Workload_result.recheck path ~expected_digest with
     | Ok () -> emit (`String "verified") | Error e -> prerr_endline e;exit 2)
  | _ -> exit 2
