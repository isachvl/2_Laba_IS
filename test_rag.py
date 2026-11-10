import json
import unittest
from evaluation import retrieval_metrics, rationale_present
from rag import parse_answer

class RequiredTests(unittest.TestCase):
    def test_retrieval_example(self):
        m=retrieval_metrics({'a','b'},['x','y','a'],3)
        self.assertEqual(m,{'hit':1,'recall':.5,'rr':1/3})
        self.assertEqual(retrieval_metrics({'a','b'},['x','y','a'],1),{'hit':0,'recall':0,'rr':0})
    def test_partial_rationale_is_not_enough(self):
        gold={'a':[{'sentences':[1,3],'label':'SUPPORT'}]}
        self.assertTrue(rationale_present(gold,{'a':[1,2,3]}))
        self.assertFalse(rationale_present(gold,{'a':[1,2]}))
    def test_parser_rejects_wrong_answers(self):
        valid={'label':'SUPPORT','doc_id':'a','sentences':[1]}
        self.assertEqual(parse_answer(json.dumps(valid),'rag',{'a':[1]}),valid)
        for invalid in [{**valid,'label':'MAYBE'},{**valid,'extra':1},{**valid,'sentences':[2]},
                        {**valid,'sentences':[-1]},{**valid,'label':'NOINFO'}]:
            with self.assertRaises(Exception): parse_answer(json.dumps(invalid),'rag',{'a':[1]})
    def test_llm_only_no_citation(self):
        value={'label':'CONTRADICT','doc_id':None,'sentences':[]}
        self.assertEqual(parse_answer(json.dumps(value),'llm_only',{}),value)

if __name__=='__main__': unittest.main()
