import pytest

from sales_agent.cli import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    main(["init"])
    return tmp_path


def test_init_refuses_to_overwrite(workdir):
    assert (workdir / "config.toml").exists()
    with pytest.raises(SystemExit):
        main(["init"])


def test_import_list_show_pipeline(workdir, capsys):
    csv_path = workdir / "leads.csv"
    csv_path.write_text(
        "Company,Contact_Name,Email,Industry\n"
        "Acme,Jo Park,jo@acme.test,fintech\n"
        "Acme,Jo Park,jo@acme.test,fintech\n"
        ",Nobody,,\n"
        "Beta Inc,,,retail\n"
    )
    main(["import", str(csv_path)])
    assert "Imported 2 lead(s), skipped 2" in capsys.readouterr().out

    main(["leads", "fintech"])
    out = capsys.readouterr().out
    assert "Acme" in out and "Beta Inc" not in out

    main(["show", "1"])
    assert "jo@acme.test" in capsys.readouterr().out

    main(["pipeline"])
    assert '"new": 2' in capsys.readouterr().out
    assert (workdir / "sales.db").exists()


def test_import_requires_company_column(workdir):
    bad = workdir / "bad.csv"
    bad.write_text("name\nx\n")
    with pytest.raises(SystemExit):
        main(["import", str(bad)])
