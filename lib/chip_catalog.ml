open! Core

type entry = { id : string; executable : string }
type t = { default_target : string; entries : entry list }

let filename = "hardcaml-asic-targets.json"
let entries t = t.entries
let default_target t = t.default_target
let entry_to_json e = `Assoc [ "id", `String e.id; "executable", `String e.executable ]
let to_json t =
  `Assoc
    [ "schema_version", `Int 1
    ; "default_target", `String t.default_target
    ; "targets", `List (List.map t.entries ~f:entry_to_json)
    ]
;;

let select t ~id =
  let id = Option.value id ~default:t.default_target in
  match List.find t.entries ~f:(fun entry -> String.equal entry.id id) with
  | Some entry -> Ok entry
  | None ->
    Or_error.errorf "Unknown chip target %S; registered IDs: %s" id
      (String.concat ~sep:", " (List.map t.entries ~f:(fun entry -> entry.id)))
;;

let object_fields where keys = function
  | `Assoc fields ->
    let fields = String.Map.of_alist_or_error fields |> Or_error.tag ~tag:where |> Or_error.ok_exn in
    Map.iter_keys fields ~f:(fun k ->
      if not (List.mem keys k ~equal:String.equal) then failwithf "%s: unknown field %S" where k ());
    (fun k -> match Map.find fields k with
       | Some v -> v
       | None -> failwithf "%s: missing %s" where k ())
  | _ -> failwithf "%s: expected an object" where ()
;;

let string key = function
  | `String s -> s
  | _ -> failwithf "%s: expected a string" key ()
;;

let of_json json =
  Or_error.try_with (fun () ->
    let field = object_fields "chip catalog" [ "schema_version"; "default_target"; "targets" ] json in
    (match field "schema_version" with
     | `Int 1 -> ()
     | _ -> failwith "Unsupported chip catalog schema_version; expected 1");
    let default_target = string "default_target" (field "default_target") in
    let entries =
      match field "targets" with
      | `List (_ :: _ as xs) ->
        List.mapi xs ~f:(fun i x ->
          let field = object_fields (sprintf "targets[%d]" i) [ "id"; "executable" ] x in
          let id = string "id" (field "id") in
          if Or_error.is_error (Hardcaml_asic_flow_domain.Selection.Id.create id)
          then failwithf "Invalid chip target ID %S; use [a-z][a-z0-9_-]*" id ();
          let executable = string "executable" (field "executable") in
          if not (String.is_suffix executable ~suffix:".exe")
             || String.exists executable ~f:(fun c ->
               Char.to_int c < 32 || Char.to_int c = 127 || Char.equal c '\\')
             || List.exists (String.split executable ~on:'/') ~f:(function
               | "" | "." | ".." | "_build" -> true
               | _ -> false)
          then failwithf "Target %S: expected a project-relative Dune .exe target, got %S" id executable ();
          { id; executable })
      | _ -> failwith "targets: expected a nonempty array"
    in
    (match List.find_a_dup entries ~compare:(fun a b -> String.compare a.id b.id) with
     | Some e -> failwithf "Duplicate chip target ID %S; assign each entry a unique ID" e.id ()
     | None -> ());
    let t = { default_target; entries } in
    select t ~id:None |> Or_error.tag ~tag:"default_target" |> Or_error.ok_exn |> ignore;
    t)
;;

let load path =
  Or_error.try_with_join (fun () -> of_json (Yojson.Safe.from_file path))
  |> Or_error.tag ~tag:path
;;
