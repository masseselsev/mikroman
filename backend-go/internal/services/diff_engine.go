package services

import (
	"fmt"
	"strings"
)

// DiffLine represents a single line in a diff hunk.
type DiffLine struct {
	Type      string `json:"type"` // "add", "del", "ctx"
	Content   string `json:"content"`
	OldLineNo *int   `json:"old_line_no,omitempty"`
	NewLineNo *int   `json:"new_line_no,omitempty"`
}

// DiffHunk represents a unified diff hunk.
type DiffHunk struct {
	OldStart int        `json:"old_start"`
	OldCount int        `json:"old_count"`
	NewStart int        `json:"new_start"`
	NewCount int        `json:"new_count"`
	Header   string     `json:"header"`
	Lines    []DiffLine `json:"lines"`
}

// DiffResult holds full diff computation output.
type DiffResult struct {
	BaseID       *int       `json:"base_id"`
	TargetID     *int       `json:"target_id"`
	IsTargetLive bool       `json:"is_target_live"`
	LinesAdded   int        `json:"lines_added"`
	LinesRemoved int        `json:"lines_removed"`
	TotalChanges int        `json:"total_changes"`
	Hunks        []DiffHunk `json:"hunks"`
	RawUnified   string     `json:"raw_unified"`
}

type diffOp struct {
	op   int // 0: equal (ctx), -1: delete (del), 1: insert (add)
	line string
}

// DiffTexts computes a unified diff between two configuration text scripts.
func DiffTexts(baseText, targetText, fromFile, toFile string, contextLines int, baseID, targetID *int, isTargetLive bool) DiffResult {
	if fromFile == "" {
		fromFile = "base.rsc"
	}
	if toFile == "" {
		toFile = "target.rsc"
	}
	if contextLines <= 0 {
		contextLines = 3
	}

	baseLines := splitLines(baseText)
	targetLines := splitLines(targetText)

	ops := myersDiff(baseLines, targetLines)

	res := DiffResult{
		BaseID:       baseID,
		TargetID:     targetID,
		IsTargetLive: isTargetLive,
		Hunks:        []DiffHunk{},
	}

	// Count additions and deletions
	for _, op := range ops {
		if op.op == 1 {
			res.LinesAdded++
		} else if op.op == -1 {
			res.LinesRemoved++
		}
	}
	res.TotalChanges = res.LinesAdded + res.LinesRemoved

	if res.TotalChanges == 0 {
		return res
	}

	// Group into hunks with context
	res.Hunks = buildHunks(ops, contextLines)

	// Format raw unified diff
	var raw strings.Builder
	raw.WriteString(fmt.Sprintf("--- %s\n", fromFile))
	raw.WriteString(fmt.Sprintf("+++ %s\n", toFile))
	for _, hunk := range res.Hunks {
		raw.WriteString(hunk.Header)
		raw.WriteByte('\n')
		for _, line := range hunk.Lines {
			prefix := " "
			if line.Type == "add" {
				prefix = "+"
			} else if line.Type == "del" {
				prefix = "-"
			}
			raw.WriteString(prefix)
			raw.WriteString(line.Content)
			raw.WriteByte('\n')
		}
	}
	res.RawUnified = raw.String()

	return res
}

func splitLines(s string) []string {
	if s == "" {
		return []string{}
	}
	s = strings.ReplaceAll(s, "\r\n", "\n")
	s = strings.ReplaceAll(s, "\r", "\n")
	lines := strings.Split(s, "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

func myersDiff(a, b []string) []diffOp {
	n := len(a)
	m := len(b)
	max := n + m
	if max == 0 {
		return nil
	}

	v := make([]int, 2*max+1)
	offset := max
	trace := make([][]int, 0, max+1)

	for d := 0; d <= max; d++ {
		vCopy := make([]int, len(v))
		copy(vCopy, v)
		trace = append(trace, vCopy)

		for k := -d; k <= d; k += 2 {
			var x int
			if k == -d || (k != d && v[k-1+offset] < v[k+1+offset]) {
				x = v[k+1+offset]
			} else {
				x = v[k-1+offset] + 1
			}
			y := x - k

			for x < n && y < m && a[x] == b[y] {
				x++
				y++
			}
			v[k+offset] = x

			if x >= n && y >= m {
				return backtrackMyers(a, b, trace, offset)
			}
		}
	}

	return backtrackMyers(a, b, trace, offset)
}

func backtrackMyers(a, b []string, trace [][]int, offset int) []diffOp {
	x := len(a)
	y := len(b)
	var ops []diffOp

	for d := len(trace) - 1; d >= 0; d-- {
		v := trace[d]
		k := x - y

		var prevK int
		if k == -d || (k != d && v[k-1+offset] < v[k+1+offset]) {
			prevK = k + 1
		} else {
			prevK = k - 1
		}

		prevX := v[prevK+offset]
		prevY := prevX - prevK

		for x > prevX && y > prevY {
			ops = append(ops, diffOp{op: 0, line: a[x-1]})
			x--
			y--
		}

		if d > 0 {
			if x == prevX {
				ops = append(ops, diffOp{op: 1, line: b[prevY]})
			} else if y == prevY {
				ops = append(ops, diffOp{op: -1, line: a[prevX]})
			}
		}

		x = prevX
		y = prevY
	}

	// Reverse ops
	for i, j := 0, len(ops)-1; i < j; i, j = i+1, j-1 {
		ops[i], ops[j] = ops[j], ops[i]
	}

	return ops
}

type annotatedOp struct {
	op        int
	line      string
	oldLineNo int
	newLineNo int
}

func buildHunks(ops []diffOp, ctx int) []DiffHunk {
	var annotated []annotatedOp
	curOld := 1
	curNew := 1

	for _, op := range ops {
		ao := annotatedOp{op: op.op, line: op.line}
		if op.op == 0 {
			ao.oldLineNo = curOld
			ao.newLineNo = curNew
			curOld++
			curNew++
		} else if op.op == -1 {
			ao.oldLineNo = curOld
			curOld++
		} else if op.op == 1 {
			ao.newLineNo = curNew
			curNew++
		}
		annotated = append(annotated, ao)
	}

	// Find changed indices
	var changeIndices []int
	for i, ao := range annotated {
		if ao.op != 0 {
			changeIndices = append(changeIndices, i)
		}
	}

	if len(changeIndices) == 0 {
		return nil
	}

	// Group ranges
	type indexRange struct {
		start int
		end   int
	}
	var groups []indexRange
	groupStart := maxInt(0, changeIndices[0]-ctx)
	groupEnd := minInt(len(annotated)-1, changeIndices[0]+ctx)

	for i := 1; i < len(changeIndices); i++ {
		cIdx := changeIndices[i]
		nextStart := maxInt(0, cIdx-ctx)
		nextEnd := minInt(len(annotated)-1, cIdx+ctx)

		if nextStart <= groupEnd+1 {
			groupEnd = nextEnd
		} else {
			groups = append(groups, indexRange{start: groupStart, end: groupEnd})
			groupStart = nextStart
			groupEnd = nextEnd
		}
	}
	groups = append(groups, indexRange{start: groupStart, end: groupEnd})

	var hunks []DiffHunk
	for _, grp := range groups {
		var hLines []DiffLine
		oldStart := 0
		oldCount := 0
		newStart := 0
		newCount := 0

		for idx := grp.start; idx <= grp.end; idx++ {
			ao := annotated[idx]
			dl := DiffLine{Content: ao.line}

			switch ao.op {
			case 0:
				dl.Type = "ctx"
				oNo := ao.oldLineNo
				nNo := ao.newLineNo
				dl.OldLineNo = &oNo
				dl.NewLineNo = &nNo
				if oldStart == 0 {
					oldStart = ao.oldLineNo
				}
				if newStart == 0 {
					newStart = ao.newLineNo
				}
				oldCount++
				newCount++
			case -1:
				dl.Type = "del"
				oNo := ao.oldLineNo
				dl.OldLineNo = &oNo
				if oldStart == 0 {
					oldStart = ao.oldLineNo
				}
				if newStart == 0 {
					newStart = curNewAt(annotated, idx)
				}
				oldCount++
			case 1:
				dl.Type = "add"
				nNo := ao.newLineNo
				dl.NewLineNo = &nNo
				if newStart == 0 {
					newStart = ao.newLineNo
				}
				if oldStart == 0 {
					oldStart = curOldAt(annotated, idx)
				}
				newCount++
			}
			hLines = append(hLines, dl)
		}

		if oldStart == 0 {
			oldStart = 1
		}
		if newStart == 0 {
			newStart = 1
		}

		header := fmt.Sprintf("@@ -%d,%d +%d,%d @@", oldStart, oldCount, newStart, newCount)
		hunks = append(hunks, DiffHunk{
			OldStart: oldStart,
			OldCount: oldCount,
			NewStart: newStart,
			NewCount: newCount,
			Header:   header,
			Lines:    hLines,
		})
	}

	return hunks
}

func curOldAt(annotated []annotatedOp, idx int) int {
	for i := idx; i >= 0; i-- {
		if annotated[i].oldLineNo > 0 {
			return annotated[i].oldLineNo + 1
		}
	}
	return 1
}

func curNewAt(annotated []annotatedOp, idx int) int {
	for i := idx; i >= 0; i-- {
		if annotated[i].newLineNo > 0 {
			return annotated[i].newLineNo + 1
		}
	}
	return 1
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

