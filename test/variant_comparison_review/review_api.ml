open Hardcaml_asic_execution
let () =
  try
    match Array.to_list Sys.argv with
    | [_; "archive-inspect"; root] ->
      let inspected = Archive_service.inspect ~run_dir:root in
      Printf.printf "{\"bundle_files\":%d,\"reports\":%d}\n"
        (List.length inspected.bundle_files) (List.length inspected.reports)
    | _ -> invalid_arg "review_api archive-inspect RUN"
  with Invalid_argument e | Failure e | Sys_error e -> prerr_endline e; exit 2
