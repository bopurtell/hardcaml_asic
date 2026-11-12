open Hardcaml_asic_execution

let () =
  try
    if Array.length Sys.argv = 5 && Sys.argv.(4) = "--capture" then (
      let observation =
        Option.get
          (Timing_analysis.capture
             ~execution:Sys.argv.(1)
             ~transport_records:[ Sys.argv.(2), Sys.argv.(3) ])
      in
      Timing_analysis.validate_sources observation;
      print_endline (Identity_json.canonical observation))
    else (
      Timing_analysis.publish
        ~execution:Sys.argv.(1)
        ~transport_records:[ Sys.argv.(2), Sys.argv.(3) ];
      print_endline
        (Identity_json.canonical (Option.get (Timing_analysis.load_file Sys.argv.(1)))))
  with
  | exn ->
    prerr_endline (Printexc.to_string exn);
    exit 2
;;
