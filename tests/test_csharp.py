from pathlib import Path

from crapper.discover import is_test_file, language_of
from crapper.languages.csharp import functions_in_source
from crapper.runners import csharp_coverage_command, csharp_modules, merge_lcov_reports


def _by_name(source: str) -> dict[str, object]:
    return {function.name: function for function in functions_in_source(source, "a.cs")}


def test_counts_statements_catches_and_logic():
    found = _by_name(
        """
namespace Demo;
class Board {
    int Score(bool a, bool b, int[] values) {
        for (var i = 0; i < values.Length; i++) { }
        foreach (var value in values) { }
        while (a) { a = false; }
        do { b = false; } while (b);
        if (a && b || values.Length > 0) { }
        try { return a ? 1 : 0; }
        catch (System.Exception ex) when (ex.Message != null) { return 2; }
        catch { return 3; }
    }
}
"""
    )
    # 1 + for + foreach + while + do + if + && + || + ?: + catch + when + catch
    assert found["Score"].complexity == 12
    assert found["Score"].namespace == "Demo.Board"


def test_cases_count_but_default_and_discard_do_not():
    found = _by_name(
        """
class Board {
    int Place(object o, int v) {
        switch (v) { case 1: case 2: break; case int n when n > 3: break; default: break; }
        return o switch { int i => 1, string s => 2, _ => 3 };
    }
}
"""
    )
    # 1 + three cases + when + two non-discard arms
    assert found["Place"].complexity == 7


def test_null_operators_and_patterns_count_but_negation_does_not():
    found = _by_name(
        """
class Board {
    int Read(Board b, object o) {
        if (!Ready && !Done) { }
        if (o is not null) { }
        return (b?.Size ?? 0) + (o is int or long ? 1 : 0);
    }
}
"""
    )
    # 1 + if + && + if + ?. + ?? + or + ?:
    assert found["Read"].complexity == 8


def test_members_accessors_and_nested_types():
    found = functions_in_source(
        """
namespace Outer {
    namespace Inner {
        class Board {
            public Board(int x) { }
            ~Board() { }
            public int Size { get { return size > 0 ? size : 1; } set => size = value; }
            public int Area => Size * Size;
            public int this[int i] => i;
            public static Board operator +(Board a, Board b) => a;
            public abstract void Draw();
            struct Cell { void Mark() { } }
        }
    }
}
""",
        "a.cs",
    )
    names = {(function.namespace, function.name) for function in found}
    assert names == {
        ("Outer.Inner.Board", "Board"),
        ("Outer.Inner.Board", "~Board"),
        ("Outer.Inner.Board", "Size.get"),
        ("Outer.Inner.Board", "Size.set"),
        ("Outer.Inner.Board", "Area.get"),
        ("Outer.Inner.Board", "this[].get"),
        ("Outer.Inner.Board", "operator +"),
        ("Outer.Inner.Board.Cell", "Mark"),
    }


def test_explicit_interface_members_carry_the_interface():
    found = functions_in_source(
        """
class Storage : IStorage {
    public int Load(int id) => id;
    int IStorage.Load(int id) => Load(id);
    int IStorage.Size { get { return 1; } }
    string IList<string>.this[int i] => "";
}
""",
        "a.cs",
    )
    assert [function.name for function in found] == [
        "Load",
        "IStorage.Load",
        "IStorage.Size.get",
        "IList<string>.this[].get",
    ]


def test_lambdas_and_local_functions_stay_in_the_member():
    found = _by_name(
        """
class Board {
    int Sum(int[] values) {
        int Pick(int v) => v > 0 ? v : 0;
        System.Func<int, bool> keep = v => v > 1 && v < 9;
        return Pick(values[0]);
    }
}
"""
    )
    assert set(found) == {"Sum"}
    assert found["Sum"].complexity == 3


def test_discovery_skips_generated_files_and_test_projects():
    assert language_of("src/Board.cs") == "csharp"
    assert language_of("obj/Board.g.cs") is None
    assert language_of("src/Form1.Designer.cs") is None
    assert is_test_file(Path("src/Demo.Tests/BoardTests.cs"))
    assert not is_test_file(Path("src/Demo/Board.cs"))


def test_modules_prefer_the_nearest_solution(tmp_path):
    (tmp_path / "Demo.sln").write_text("", encoding="utf-8")
    project = tmp_path / "src" / "Demo"
    project.mkdir(parents=True)
    (project / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    source = project / "Board.cs"
    source.write_text("class Board {}", encoding="utf-8")
    assert csharp_modules(tmp_path, [source]) == [tmp_path.resolve()]


def test_modules_fall_back_to_the_project(tmp_path):
    project = tmp_path / "Demo"
    project.mkdir()
    (project / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    source = project / "Board.cs"
    source.write_text("class Board {}", encoding="utf-8")
    assert csharp_modules(tmp_path, [source]) == [project.resolve()]


def test_coverage_command_asks_coverlet_for_lcov(tmp_path):
    command = csharp_coverage_command(tmp_path)
    assert command[:4] == ["dotnet", "test", "--collect", "XPlat Code Coverage"]
    assert command[-1].endswith("Format=lcov")


def test_merge_sums_hits_across_test_projects():
    first = "SF:/src/Board.cs\nDA:3,0\nDA:4,2\nBRDA:3,0,0,0\nBRDA:3,0,1,-\nend_of_record\n"
    second = "SF:/src/Board.cs\nDA:3,1\nDA:4,0\nBRDA:3,0,0,1\nBRDA:3,0,1,0\nend_of_record\n"
    assert merge_lcov_reports([first, second]) == (
        "SF:/src/Board.cs\nDA:3,1\nDA:4,2\nBRDA:3,0,0,1\nBRDA:3,0,1,0\nend_of_record\n"
    )
