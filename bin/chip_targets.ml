module Catalog = Hardcaml_asic.Chip_catalog

let usage =
  "hardcaml-asic-targets (list [--catalog PATH] | select [--catalog PATH] [--target ID] | exec [--catalog PATH] [--target ID] -- ARG...)"

let checked = function Ok x -> x | Error e -> failwith (Core.Error.to_string_hum e)
let print json = print_endline (Yojson.Safe.to_string json)

let run command args =
  let rec parse catalog target = function
    | "--catalog" :: path :: rest when catalog = None && path <> "" ->
      parse (Some path) target rest
    | "--target" :: id :: rest when command <> "list" && target = None ->
      parse catalog (Some id) rest
    | "--" :: argv when command = "exec" -> catalog, target, argv
    | [] when command <> "exec" -> catalog, target, []
    | _ -> failwith usage
  in
  let path, id, argv = parse None None args in
  let path = Option.value path ~default:Catalog.filename in
  let catalog = checked (Catalog.load path) in
  match command with
  | "list" -> print (Catalog.to_json catalog)
  | "select" -> print (Catalog.entry_to_json (checked (Catalog.select catalog ~id)))
  | _ ->
    let entry = checked (Catalog.select catalog ~id) in
    let root = Unix.realpath (Filename.dirname path) in
    Unix.chdir root;
    Unix.execvp "dune"
      (Array.of_list (["dune"; "exec"; "--root"; root; "./" ^ entry.executable; "--"] @ argv))
;;

let () =
  try
    match Array.to_list Sys.argv with
    | [_; ("--help" | "-h")] -> print_endline usage
    | _ :: ("list" | "select" | "exec" as command) :: args -> run command args
    | _ -> failwith usage
  with
  | Failure message | Sys_error message -> prerr_endline message; exit 2
  | Unix.Unix_error (err, func, arg) ->
    Printf.eprintf "%s %s: %s\n" func arg (Unix.error_message err); exit 2
