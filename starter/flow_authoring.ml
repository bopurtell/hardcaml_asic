open Hardcaml_asic

let checked = Core.Or_error.ok_exn
let recipe = "librelane_classic_v1"
let describe =
  `Assoc ["schema_version", `Int 1; "recipes", `List [
    `Assoc ["id", `String recipe; "endpoints",
      `List (List.map (fun s -> `String (Flow.Stage.to_string s)) Flow.Stage.all)]]]

let profile_source ~id ~assignments =
  Printf.sprintf
    "open Hardcaml_asic\n\nlet create () =\n  Flow.Profile.create ~id:%S ~label:%S ~description:%S\n    ~assignments:[%s]\n"
    id (String.capitalize_ascii id) "" (String.concat "; " assignments)

let flow_source ~id ~profile ~endpoints =
  Printf.sprintf {|%s
open Hardcaml_asic

let create ~additional_profiles =
  let open Core.Or_error in
  bind (Flow.Methodology.create ~id:%S ~label:%S
    ~description:"LibreLane physical-stage prefixes" ~backend_recipe:Librelane_classic_v1
    ~endpoints:%s ~configurable:Flow.Parameter.Key.all ~defaults:[]) ~f:(fun methodology ->
  bind (Flow.Profile.create ~id:%S ~label:%S ~description:"" ~assignments:[]) ~f:(fun profile ->
  Flow.Catalog.Entry.create ~methodology ~profiles:(profile :: additional_profiles)
    ~default_profile:%S))
|} Collection.profile_marker id (String.capitalize_ascii id) endpoints profile (String.capitalize_ascii profile) profile

let render ~valid_name command json =
  let open Yojson.Safe.Util in
  let j = Yojson.Safe.from_string json in
  let text k = member k j |> to_string in
  let owner = text "owner" and flow = text "flow_id" and profile = text "profile_id" in
  let registered = member "registered" j |> to_bool in
  let check_module s =
    if not (valid_name s) || String.contains s '-' then failwith ("Invalid module ID: " ^ s)
  in
  if (owner <> "." || registered) && not (List.for_all (fun s -> s <> "" && s <> "." && s <> ".."
     && not (String.contains s '\\') && not (String.contains s '\000')) (String.split_on_char '/' owner))
  then failwith "Expected relative owner directory";
  check_module profile;
  let catalog = Flow_spec.list_of_yojson (member "catalog" j) |> checked in
  let entry = Flow.Catalog.entries catalog |> List.find_opt (fun e ->
    Flow.Methodology.id (Flow.Catalog.Entry.methodology e) |> Flow.Selection.Id.to_string = flow) in
  let path, source, registration = match command with
    | "render-flow" ->
      check_module flow;
      if Option.is_some entry then failwith ("Duplicate flow ID " ^ flow);
      if text "recipe" <> recipe then failwith "Unsupported recipe";
      let stages = member "endpoints" j |> to_list |> List.map (fun x -> Flow.Stage.of_string (to_string x) |> checked) in
      if stages = [] || List.length stages <> List.length (List.sort_uniq compare stages)
      then failwith "Expected nonempty unique endpoints";
      let endpoints = "[" ^ String.concat "; " (List.filter_map (fun s ->
        if List.mem s stages then Some (String.capitalize_ascii (Flow.Stage.to_string s)) else None) Flow.Stage.all) ^ "]" in
      "flow_" ^ flow ^ ".ml", flow_source ~id:flow ~profile ~endpoints,
      Printf.sprintf "Save the declaration in Flow_%s, then include (Flow_%s.create ~additional_profiles:[] |> Core.Or_error.ok_exn) in the catalog's entries list." flow flow
    | _ ->
      let e = match entry with None -> failwith ("Unknown flow ID " ^ flow) | Some e -> e in
      let profiles = Flow.Catalog.Entry.profiles e in
      let find id = List.find_opt (fun p -> Flow.Selection.Id.to_string (Flow.Profile.id p) = id) profiles in
      if Option.is_some (find profile) then failwith ("Duplicate profile ID " ^ profile);
      let from = match find (text "source_profile") with Some p -> p | None -> failwith "Unknown source profile" in
      let configurable = Flow.Methodology.configurable (Flow.Catalog.Entry.methodology e) in
      let edits = member "edits" j |> to_assoc in
      let keys = List.map (fun (k, _) -> Flow.Parameter.Key.of_string k |> checked) edits
        |> Flow.Parameter.check_keys |> checked in
      if not (List.for_all (fun k -> List.mem k configurable) keys) then failwith "Parameter not configurable";
      let copied = Flow.Profile.assignments from |> List.filter (fun a ->
        not (List.mem (Flow.Parameter.assignment_key a) keys)) in
      let assignments = copied @ List.filter_map (function
        | _, `Null -> None
        | key, `String s -> Some (Flow.Parameter.of_string_value ~key s |> checked)
        | key, `Bool b -> Some (Flow.Parameter.of_bool_value ~key b |> checked)
        | _ -> failwith "Invalid parameter scalar") edits
        |> Flow.Parameter.check_assignments ~configurable |> checked |> List.map (fun a ->
          match Flow.Parameter.assignment_value a with
          | Bool b -> "Flow.Parameter.synth_abc_buffering " ^ string_of_bool b
          | Strategy s -> "Flow.Parameter.synth_strategy " ^
            (Flow.Parameter.Strategy.to_string s |> String.lowercase_ascii |> String.capitalize_ascii
             |> String.map (function ' ' -> '_' | c -> c))) in
      flow ^ "/profiles/" ^ profile ^ ".ml", profile_source ~id:profile ~assignments,
      Printf.sprintf "Save the declaration in module %s, then include (%s.create () |> Core.Or_error.ok_exn) in the owning entry's profiles list."
        (String.capitalize_ascii profile) (String.capitalize_ascii profile)
  in
  let directory = if owner = "." then "flows" else Filename.concat owner "flows" in
  let files = if registered then [Filename.concat directory path, source] else [] in
  files, ["manual_registration", `Bool (not registered);
          "snippet", `String (if registered then "" else source ^ "\n(* " ^ registration ^ " *)\n")]
