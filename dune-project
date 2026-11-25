(lang dune 3.17)

(name hardcaml_asic)
(version 0.1.0)

(generate_opam_files true)

(source
 (github bopurtell/hardcaml_asic))

(authors "Bohdan Purtell <bohdanpurtell@gmail.com>")
(license MIT)
(maintainers "Bohdan Purtell <bohdanpurtell@gmail.com>")

(package
 (name hardcaml_asic)
 (synopsis "Hardcaml ASIC project and Tiny Tapeout bundle library, with the flow CLI")
 (description
  "Project elaboration, registered resources, and TT/LibreLane bundle emission, \
installed together with the hardcaml-asic-flow command and its runtime data so a \
consumer pins one revision for both.

Host requirements beyond the OCaml dependencies: Python 3.9 or newer for the \
flow command, and a POSIX shell. Provisioning additionally needs Bash, Git and \
the usual Unix utilities; running and postchecking a design need Docker, \
LibreLane and Nix. None of those are fetched by installing this package, and \
the record operations -- version, help, collect, report, archive -- need none \
of them.")
 (depends
  (ocaml (>= 5.1))
  core
  hardcaml
  yojson
  cryptokit
  zarith
  spawn
  ppx_jane
  ppx_hardcaml
  ; Host Python for the installed hardcaml-asic-flow command. conf-python-3 does
  ; not express a minor version, so the >= 3.9 bound the flow modules actually
  ; need is checked by the command itself and stated in the description above.
  conf-python-3
  (expect_test_helpers_core :with-test)))
