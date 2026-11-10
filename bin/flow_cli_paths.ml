let lexical p =
  let p=if Filename.is_relative p then Filename.concat (Sys.getcwd ()) p else p in
  "/" ^ String.concat "/" (List.filter (fun s -> s<>"" && s<>".") (String.split_on_char '/' p))
(* Resolve existing symlink ancestors even for an as-yet absent destination. *)
let absolute p =
  let rec walk budget current = function
    | [] -> current
    | ""::xs | "."::xs -> walk budget current xs
    | ".."::xs -> walk budget (Filename.dirname current) xs
    | x::xs ->
      let next=Filename.concat current x in
      let target=try Some (Unix.readlink next) with Unix.Unix_error ((Unix.EINVAL|Unix.ENOENT|Unix.ENOTDIR),_,_) -> None in
      match target with
      | None -> walk budget next xs
      | Some t -> if budget=0 then invalid_arg "symlink loop while resolving path";
        walk (budget-1) (if Filename.is_relative t then current else "/") (String.split_on_char '/' t @ xs) in
  walk 40 "/" (String.split_on_char '/' (lexical p))
let executable () = Unix.readlink "/proc/self/exe"
let development () =
  let exe=executable () in
  Filename.basename exe="flow_cli.exe" &&
  Filename.basename (Filename.dirname (Filename.dirname exe))="default" &&
   List.mem (Filename.basename (Filename.dirname (Filename.dirname (Filename.dirname exe))))
      ["_build";"_build_pt8_workloads";"_build_pt8_workbench"]
let resources () =
  let bin=Filename.dirname (executable ()) in
  (* Only the development build has an explicit build-tree layout. Installed
     execution never searches a checkout, environment prefix or PATH. *)
  if development ()
  then Filename.dirname bin ^ "/flow/hardcaml_asic_flow"
  else Filename.dirname bin ^ "/lib/hardcaml_asic/flow/hardcaml_asic_flow"
let helper name =
  let bin=Filename.dirname (executable ()) in
  let development=development () in
  let p=bin^"/"^(if development then "experimental_"^String.map (fun c -> if c='-' then '_' else c) name^".exe"
    else "hardcaml-asic-experimental-"^name) in
  (try Unix.access p [Unix.X_OK] with Unix.Unix_error _ ->
    failwith ("OCaml backend is missing: "^p^". Reinstall hardcaml_asic into this prefix (development: dune build -j20 @install).")); p
