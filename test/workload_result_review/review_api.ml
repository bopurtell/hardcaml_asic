open Hardcaml_asic_execution
let emit j = print_endline (Evidence_json.record_bytes j)
let error e = prerr_endline e;exit 2
let () = match Array.to_list Sys.argv with
  | [_;"variant";depth] ->
    let module H=Hardcaml_asic.Hardware_variant in
    let ok=Core.Or_error.ok_exn in
    let p=ok (H.Parameter.create ~key:"depth" ~kind:(Integer {minimum=1;maximum=512})) in
    let schema=ok (H.Schema.create ~design_id:"independent-review-memory" ~schema_id:"review.v1" ~parameters:[p]) in
    emit (H.to_yojson (ok (H.create ~schema ~assignments:["depth",H.Value.Int (int_of_string depth)])))
  | [_;path] -> (match Workload_result.inspect path with Ok j -> emit j | Error e -> error e)
  | [_;"descriptor-probe";path;count] ->
    let descriptors () = Array.length (Sys.readdir "/proc/self/fd") in
    let before=descriptors () and errors=ref 0 in
    for _=1 to int_of_string count do
      match Workload_result.inspect path with Ok _ -> () | Error _ -> incr errors
    done;
    let after=descriptors () in
    emit (`Assoc ["before",`Int before;"after",`Int after;"errors",`Int !errors;
      "growth",`Int (after-before)])
  | [_;path;expected_digest] ->
    (match Workload_result.recheck path ~expected_digest with Ok () -> emit (`String "verified") | Error e -> error e)
  | _ -> exit 2
