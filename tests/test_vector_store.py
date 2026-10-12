import tempfile
import unittest
from pathlib import Path
from bubot.vector_store import open_client, open_collection


class VectorStoreTests(unittest.TestCase):
    def test_cosine_order_persistence_and_empty_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            col = open_collection(open_client(path), 'test', create=True)
            col.upsert(['same', 'orthogonal', 'opposite'], ['a', 'b', 'c'],
                       [{'region_id':'1'}]*3, [[10,0], [0,2], [-3,0]])
            col = open_collection(open_client(path), 'test')
            result = col.query([[2,0]], 3)
            self.assertEqual(result['ids'][0], ['same','orthogonal','opposite'])
            self.assertEqual(result['distances'][0], [0,1,2])
            self.assertEqual(col.query([[1,0]], 3, where={'region_id':{'$eq':'99'}})['ids'], [[]])
            for vector in ([0,0], [float('nan'),1], [1,2,3]):
                with self.assertRaises(ValueError):
                    col.upsert(['bad'], ['d'], [{}], [vector])
            self.assertEqual(col.count(), 3)

    def test_memory_cache_refreshes_after_upsert(self):
        with tempfile.TemporaryDirectory() as directory:
            col = open_collection(open_client(Path(directory)), 'test', create=True)
            col.upsert(['a'], ['a'], [{}], [[1, 0]])
            self.assertEqual(col.query([[0, 1]], 5)['ids'], [['a']])
            col.upsert(['b'], ['b'], [{}], [[0, 1]])
            self.assertEqual(open_collection(open_client(Path(directory)), 'test').query([[0, 1]], 5)['ids'], [['b', 'a']])
            col.upsert(['a'], ['a2'], [{'v': 2}], [[0, 1]])
            result = col.query([[0, 1]], 5)
            self.assertEqual(result['documents'], [['a2', 'b']])
            result['metadatas'][0][0]['v'] = 99  # 반환값 수정이 캐시에 섞이지 않아야 합니다
            self.assertEqual(col.query([[0, 1]], 1)['metadatas'], [[{'v': 2}]])
