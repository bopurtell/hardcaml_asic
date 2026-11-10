open Hardcaml_asic_execution

let () =
  let path=ref None and json=ref false in
  try
    Arg.parse ["--json",Arg.Set json,"JSON";
      "--verbose",Arg.Unit (fun () -> ()),"Verbose"]
      (fun p -> if Option.is_some !path || String.trim p="" then
        invalid_arg "expected exactly one nonempty result path" else path:=Some p)
      "Read-only workload inspection RESULT [--json]";
    let path=match !path with Some p -> p | None -> invalid_arg "result path is required" in
    let result=match Workload_result.inspect path with Ok j -> j | Error e -> invalid_arg e in
    let bytes=Evidence_json.record_bytes result in
    if String.length bytes+1>1024*1024 then invalid_arg "workload inspection exceeds 1MiB";
    if !json then print_endline bytes else (
      print_endline "WORKLOAD INSPECTION (readable evidence; not an acceptance verdict)";
      print_endline bytes)
  with Invalid_argument e | Failure e | Sys_error e ->
    prerr_endline ("workload-inspect: "^e);exit 2
