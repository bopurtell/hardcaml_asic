open Hardcaml_asic_execution

let descriptors () = Array.length (Sys.readdir "/proc/self/fd")
let valid path = match Workload_result.inspect path with
  | Ok _ -> ()
  | Error e -> failwith ("valid evidence rejected: " ^ e)

let () =
  match Array.to_list Sys.argv with
  | _ :: positive :: rejected when rejected<>[] ->
    valid positive;
    let receipts=List.map (fun path ->
      let before=descriptors () in
      for i=1 to 128 do
        (match Workload_result.inspect path with
         | Error _ -> ()
         | Ok _ -> failwith ("directory admission unexpectedly succeeded: "^path));
        (* Exercise successful channel ownership between conversion failures,
           without relying on GC or process exit to release descriptors. *)
        if i mod 16=0 then valid positive
      done;
      let after=descriptors () in
      if after<>before then failwith (Printf.sprintf
        "directory rejection descriptor growth: %s before=%d after=%d growth=%d"
        path before after (after-before));
      `Assoc ["path",`String path;"errors",`Int 128;"before",`Int before;
        "after",`Int after;"growth",`Int (after-before)]) rejected in
    print_endline (Yojson.Safe.to_string (`List receipts))
  | _ -> failwith "expected valid evidence followed by rejected directory cases"
