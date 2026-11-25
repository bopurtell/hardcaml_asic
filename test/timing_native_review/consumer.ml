open Hardcaml_asic_execution
let field j k=match j with `Assoc xs -> List.assoc k xs | _ -> invalid_arg "object required"
let text = function `String s -> s | _ -> invalid_arg "string required"
let result = function
  | Ok j -> `Assoc ["ok",`Bool true;"value",j]
  | Error e -> `Assoc ["ok",`Bool false;"error",`String e]
let () =
  if Sys.argv.(1)="seal-manifest" then (
    let j=Yojson.Safe.from_file Sys.argv.(2) in
    let fields=match j with `Assoc xs -> List.remove_assoc "identity" xs | _ -> invalid_arg "manifest object required" in
    let identity=Identity_json.sha256_bytes (Yojson.Safe.to_string (`Assoc fields)) in
    print_endline (Yojson.Safe.to_string (`Assoc (("identity",`String identity)::fields))))
  else if Sys.argv.(1)="producer" then print_string (Hardcaml_asic.Elaboration.Timing_coverage_tcl.render ())
  else (
    let value=if Sys.argv.(1)="audit" then Timing_intent_evidence.inspect ~directory:Sys.argv.(2) ()
      else (
        let j=Yojson.Safe.from_file Sys.argv.(2) in
        let resolved=match field j "resolved" with `List xs -> xs | _ -> invalid_arg "array required" in
        Timing_native_coverage.inspect ~top:(text (field j "top")) ~resolved
          ~native_sdc:(text (field j "native_sdc")) ~native_data:(text (field j "native_data"))
          ~native_checks:(text (field j "native_checks")) ~native_endpoints:(text (field j "native_endpoints"))) in
    print_endline (Yojson.Safe.to_string (result value)))
