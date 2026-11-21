(* [paths] is Dune's (source_tree .), not a search of the project or build tree. *)
let profile_marker = "(* hardcaml-asic profile directory v1 *)"

let generate root paths =
  let split p = String.split_on_char '/' p |> List.filter ((<>) ".") in
  let paths = List.map (fun p -> String.concat "/" (split p)) paths |> List.sort_uniq String.compare in
  let entries = List.filter_map (fun p ->
    match split p with
    | [f] when Filename.check_suffix f ".ml" && String.starts_with ~prefix:"flow_" f ->
      let name = Filename.chop_suffix f ".ml" in
      Some (String.sub name 5 (String.length name - 5), String.capitalize_ascii name)
    | _ -> None) paths in
  let profile_owners = List.filter_map (fun (id, _) ->
    let lines = In_channel.with_open_bin ("flow_" ^ id ^ ".ml") In_channel.input_lines in
    if List.mem profile_marker lines then Some id else None) entries in
  let profiles = List.filter_map (fun p -> match split p with
    | [id; "profiles"; file] when Filename.check_suffix file ".ml" ->
      if not (List.mem_assoc id entries) then failwith ("No flow_" ^ id ^ ".ml owns " ^ p);
      if List.mem id profile_owners then Some (id, p) else None
    | _ -> None) paths in
  Printf.printf "let source_inputs = [\n";
  List.iter (fun p -> match split p with
    | ["dune"] | [_] when Filename.basename p = "dune" || Filename.check_suffix p ".ml"
                         || Filename.check_suffix p ".mli" ->
      Printf.printf "  %S;\n" (Filename.concat root p)
    | [id; "profiles"; _] when List.mem id profile_owners && Filename.check_suffix p ".ml" ->
      Printf.printf "  %S;\n" (Filename.concat root p)
    | _ -> ()) paths;
  Printf.printf "]\n";
  List.iteri (fun n (_, p) ->
    Printf.printf "module Profile_%d = struct\n# 1 %S\n%s\nend\n" n
      (Filename.concat root p) (In_channel.with_open_bin p In_channel.input_all)) profiles;
  Printf.printf "let create ~default_flow ~default_profile =\n  let open Core.Or_error in\n  bind (combine_errors [\n";
  List.iter (fun (id, entry) ->
    Printf.printf "    bind (combine_errors [" ;
    List.iteri (fun n (owner, _) -> if owner = id then Printf.printf "Profile_%d.create (); " n) profiles;
    Printf.printf "]) ~f:(fun additional_profiles -> %s.create ~additional_profiles);\n" entry) entries;
  Printf.printf "  ]) ~f:(fun entries -> Hardcaml_asic.Flow.Catalog.create ~entries ~default_flow ~default_profile)\n"
;;

let stanza ~root ~library =
  Printf.sprintf {|; hardcaml-asic flow collection v1
(rule
 (target generated.ml)
 (deps (source_tree .))
 (action (with-stdout-to %%{target}
   (run %%{bin:hardcaml-asic-starter} generate-collection %s %%{deps}))))

(library
 (name %s)
 (libraries core hardcaml_asic))
|} root library
;;
