//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package prompts

import (
	"strings"
	"testing"
)

func TestCitationPromptIncludesNamedSourceAndEvidenceSynthesisRules(t *testing.T) {
	prompt := CitationPrompt()
	for _, required := range []string{
		"Add citations using only the document context provided for the current turn.",
		"Previous assistant messages are conversational context, not evidence.",
		"Never use `ID:i` as a document name",
		"conclusion must not contradict an earlier evidence table",
		"Missing information in a retrieved snippet",
	} {
		if !strings.Contains(prompt, required) {
			t.Errorf("CitationPrompt() missing %q", required)
		}
	}
	if strings.Contains(prompt, "document or chat history") {
		t.Error("CitationPrompt() must not allow chat history as citation evidence")
	}
}
