open Hardcaml_asic_execution
let () =
  let execution=ref "" and domain=ref "" and stage=ref "" and row=ref "" and digest=ref "" in
   let json=ref false and verbose=ref false and refresh=ref false in
  Arg.parse ["--execution",Arg.Set_string execution,"PATH Exact execution JSON";
    "--domain",Arg.Set_string domain,"DOMAIN timing|clocks|physical|routing";
    "--stage",Arg.Set_string stage,"STAGE Published boundary";
    "--row-id",Arg.Set_string row,"ID Detail row";
     "--expected-digest",Arg.Set_string digest,"SHA256 Analysis digest for index; index digest for detail";
     "--refresh-timing",Arg.Set refresh," Reinspect exact retained Timing sources read-only; preserve original observation";
    "--json",Arg.Set json," JSON envelope";"--verbose",Arg.Set verbose," Diagnostics"]
    (fun _ -> raise (Arg.Bad "unexpected positional argument")) "Read-only retained rich inspection";
   match Inspection_service.run ~execution:!execution ~domain:!domain ~stage:!stage ~refresh_timing:!refresh
    ?row_id:(if !row="" then None else Some !row)
    ?expected_digest:(if !digest="" then None else Some !digest) () with
  | Error e -> prerr_endline ("hardcaml-asic-flow inspect: "^e);exit 2
  | Ok envelope -> print_endline (Identity_json.canonical envelope)
