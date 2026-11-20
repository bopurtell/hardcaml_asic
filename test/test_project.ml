open! Core
open! Hardcaml_asic

let%test_unit "declaration inspection and empty inventory do not construct a design" =
  let calls = ref 0 in
  let design = Fixture.design ~before:(fun _ -> incr calls) () in
  let project : Chip_target.t = Fixture.project ~design () |> Or_error.ok_exn in
  assert (List.is_empty (Project.source_inputs project));
  let description = Chip_target.describe project |> Sexp.to_string in
  List.iter [ "fixture_top"; "project_name"; "metadata"; "source_inputs";
              "target"; "flow"; "clocks"; "pinout"; "timing"; "policy" ]
    ~f:(fun substring -> assert (String.is_substring description ~substring));
  assert (!calls = 0);
  assert
    (Or_error.is_error
       (Project.emit_bundle project ~source_root:"/does/not/exist"
          ~output_dir:"/does/not/exist"));
  assert (!calls = 0);
  ignore (Project.elaborate project ~mode:Implementation |> Or_error.ok_exn
          : Elaborated_project.t);
  assert (!calls = 1)
;;
