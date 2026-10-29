from benchmarks.doclaynet.boxes import area, intersection, score, union_area


def test_union_coverage_avoids_double_counting_and_clips_to_reference():
    reference = (0, 0, 10, 10)
    predictions = [(-5, 0, 6, 10), (4, 0, 15, 10)]
    assert union_area([intersection(reference, p) for p in predictions]) == area(reference)


def test_strict_matching_is_one_to_one_and_thresholded():
    refs = [{'bbox': (0, 0, 10, 10)}]
    predictions = [{'bbox': (0, 0, 10, 10)}, {'bbox': (0, 0, 5, 10)}]
    result = score(refs, predictions, .5)
    assert result['matched'] == 1
    assert result['precision'] == .5
    assert result['recall'] == 1
    assert score(refs, predictions[1:], .75)['matched'] == 0
