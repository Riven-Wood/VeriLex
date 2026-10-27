using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using Docxodus;

void Check(bool ok,string message){if(!ok)throw new Exception(message);}
void Ok(EditResult r){Check(r.Success,r.Error?.Code+": "+r.Error?.Message);}
XElement Body(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);return new XElement(doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!);}
void Validate(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);var errors=new OpenXmlValidator(DocumentFormat.OpenXml.FileFormatVersions.Office2016).Validate(doc).ToArray();Check(errors.Length==0,"invalid OOXML: "+string.Join("; ",errors.Select(e=>e.Description)));}
string Canon(byte[] data){var b=Body(data);foreach(var a in b.DescendantsAndSelf().Attributes().Where(a=>a.Name==PtOpenXml.Unid||a.IsNamespaceDeclaration||a.Name==XNamespace.Xml+"space").ToArray())a.Remove();foreach(var e in b.Descendants().Reverse().Where(e=>(e.Name==W.pPr||e.Name==W.rPr||e.Name==W.trPr||e.Name==W.tcPr)&&!e.HasElements&&!e.HasAttributes).ToArray())e.Remove();return b.ToString(SaveOptions.DisableFormatting);}
string Table(DocxSession s)=>s.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("tbl:body:")).Last();
string Cell(DocxSession s,int r=0,int c=0)=>s.ResolveTableCellCoordinate(Table(s),r,c).Cell!.Anchor.Id;
byte[] Fixture(){using var s=new DocxSession(DocxSession.CreateBlankDocxBytes());var p=s.Project().AnchorIndex.Keys.First(k=>k.StartsWith("p:body:"));Ok(s.InsertTable(p,Position.Before,3,3,new TableInsertOptions{CellContents=Enumerable.Range(0,9).Select(i=>$"格{i/3+1}-{i%3+1}").ToArray()}));return s.Save();}
var baseline=Fixture();Validate(baseline);File.WriteAllBytes("output/contract-format-table-2026-10-24/table-engine/table-baseline.docx",baseline);
var operations=new(string name,Func<DocxSession,EditResult> op)[]{("row-before",s=>s.InsertTableRow(Cell(s,1,1),Position.Before)),("row-after",s=>s.InsertTableRow(Cell(s,1,1),Position.After)),("column-before",s=>s.InsertTableColumn(Cell(s,1,1),Position.Before)),("column-after",s=>s.InsertTableColumn(Cell(s,1,1),Position.After)),("delete-row",s=>s.DeleteTableRow(Cell(s,1,1))),("delete-column",s=>s.DeleteTableColumn(Cell(s,1,1))),("merge",s=>s.MergeCells(Cell(s),2,2))};
foreach(var(name,op) in operations){
 using var direct=new DocxSession(baseline);Ok(op(direct));Validate(direct.Save());
 using var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});Ok(op(s));var edited=s.Save();Validate(edited);Check(s.ListRevisions().Count>0,name+" missing revisions");
 Check(s.Undo(),name+" undo");Check(Canon(s.Save())==Canon(baseline),name+" undo changed original");Check(s.Redo(),name+" redo");Check(Canon(s.Save())==Canon(edited),name+" redo changed edit");
 using var accepted=new DocxSession(edited);Ok(accepted.AcceptAllRevisions());Validate(accepted.Save());Check(Canon(accepted.Save())==Canon(direct.Save()),name+" acceptance mismatch");
 using var rejected=new DocxSession(edited);Ok(rejected.RejectAllRevisions());Validate(rejected.Save());Check(Canon(rejected.Save())==Canon(baseline),name+" rejection mismatch");
 Console.WriteLine("PASS "+name);
}
using(var original=new DocxSession(baseline)){Ok(original.MergeCells(Cell(original),2,2));var merged=original.Save();using var s=new DocxSession(merged,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});Ok(s.UnmergeCells(Cell(s)));Validate(s.Save());using var r=new DocxSession(s.Save());Ok(r.RejectAllRevisions());Check(Canon(r.Save())==Canon(merged),"unmerge reject");Ok(s.AcceptAllRevisions());Validate(s.Save());Check(s.GetTableMetadata(Table(s)).Metadata!.Rows.All(row=>row.Cells.Count==3),"unmerge shape");Console.WriteLine("PASS unmerge");}
using(var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"})){
 Ok(s.InsertTableColumn(Cell(s),Position.After));Ok(s.InsertTableRow(Cell(s),Position.After));Ok(s.MergeCells(Cell(s),2,2));Ok(s.UnmergeCells(Cell(s)));Ok(s.DeleteTableColumn(Cell(s,1,1)));Ok(s.DeleteTableRow(Cell(s,1,1)));Validate(s.Save());File.WriteAllBytes("output/contract-format-table-2026-10-24/table-engine/table-sequence.docx",s.Save());
 using var r=new DocxSession(s.Save());Ok(r.RejectAllRevisions());Validate(r.Save());Check(Canon(r.Save())==Canon(baseline),"sequence reject");Ok(s.AcceptAllRevisions());Validate(s.Save());Console.WriteLine("PASS sequential edits");
}
foreach(var premerged in new[]{false,true})foreach(var dims in new[]{(1,2),(2,1),(2,2),(3,4),(1,1)}){
 using var setup=new DocxSession(baseline);if(premerged)Ok(setup.MergeCells(Cell(setup),2,2));var input=setup.Save();
 using var direct=new DocxSession(input);Ok(direct.SplitCell(Cell(direct),dims.Item1,dims.Item2));Validate(direct.Save());
 using var s=new DocxSession(input,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});Ok(s.SplitCell(Cell(s),dims.Item1,dims.Item2));var changed=s.Save();Validate(changed);
 Check(s.Undo(),"split undo");Check(Canon(s.Save())==Canon(input),"split undo original");Check(s.Redo(),"split redo");
 using var reject=new DocxSession(changed);Ok(reject.RejectAllRevisions());Validate(reject.Save());Check(Canon(reject.Save())==Canon(input),"split reject original");
 using var accept=new DocxSession(changed);Ok(accept.AcceptAllRevisions());Validate(accept.Save());Check(Canon(accept.Save())==Canon(direct.Save()),"split accept endpoint");
 Check(string.Concat(Body(accept.Save()).Descendants(W.t).Select(t=>t.Value))==string.Concat(Body(input).Descendants(W.t).Select(t=>t.Value)),"split preserves all text");
 Console.WriteLine($"PASS split {dims.Item1}x{dims.Item2} premerged={premerged}");
}
foreach(var byRow in new[]{true,false}){
 using var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 for(int i=0;i<3;i++)Ok(byRow?s.DeleteTableRow(Cell(s)):s.DeleteTableColumn(Cell(s)));
 var deleted=s.Save();Validate(deleted);Check(Body(deleted).Elements(W.tbl).Count()==1,"only deleted original remains");
 using var r=new DocxSession(deleted);Ok(r.RejectAllRevisions());Validate(r.Save());Check(Canon(r.Save())==Canon(baseline),"last table removal reject");
 Ok(s.AcceptAllRevisions());Validate(s.Save());Check(!Body(s.Save()).Elements(W.tbl).Any(),"last row/column removes table");Console.WriteLine("PASS delete entire table "+byRow);
}
using(var s=new DocxSession(DocxSession.CreateBlankDocxBytes(),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"})){
 var p=s.Project().AnchorIndex.Keys.First(k=>k.StartsWith("p:body:"));Ok(s.InsertTable(p,Position.Before,2,2));
 var paragraph=s.ResolveTableCellAnchor(Cell(s)).Cell!.ParagraphAnchors.First().Id;Ok(s.SyncKeyboardText(paragraph,"新表文字"));
 Ok(s.InsertTableColumn(Cell(s),Position.After));Ok(s.MergeCells(Cell(s),2,2));Ok(s.SplitCell(Cell(s),3,3));Ok(s.DeleteTableColumn(Cell(s)));Validate(s.Save());
 using var r=new DocxSession(s.Save());Ok(r.RejectAllRevisions());Validate(r.Save());Check(!Body(r.Save()).Elements(W.tbl).Any(),"new table rejection removes full sequence");
 Ok(s.AcceptAllRevisions());Validate(s.Save());Console.WriteLine("PASS new table text plus shape changes");
}
using(var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"})){
 var before=Canon(s.Save());var result=s.MergeCells(Cell(s),99,99);Check(!result.Success,"invalid merge must fail");if(Canon(s.Save())!=before){File.WriteAllText("output/contract-format-table-2026-10-24/table-engine/fail-before.xml",before);File.WriteAllText("output/contract-format-table-2026-10-24/table-engine/fail-after.xml",Canon(s.Save()));}Check(Canon(s.Save())==before,"failed merge modified package");
 result=s.SplitCell(Cell(s),0,2);Check(!result.Success,"invalid split must fail");Check(Canon(s.Save())==before,"failed split modified package");
 Console.WriteLine("PASS rejected operation atomicity");
}
// Comments outside the target table do not disable ordinary table editing.
using(var setup=new DocxSession(baseline)){
 var bodyP=setup.Project().AnchorIndex.Keys.Last(k=>k.StartsWith("p:body:"));Ok(setup.InsertTable(bodyP,Position.Before,2,2,new TableInsertOptions{CellContents=new[]{"批注表","二","三","四"}}));
 var paragraph=setup.ResolveTableCellAnchor(Cell(setup)).Cell!.ParagraphAnchors.First().Id;Ok(setup.AddComment(paragraph,null,"审阅者","保留批注"));
 using var s=new DocxSession(setup.Save(),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var before=Canon(s.Save());var refused=s.SplitCell(Cell(s),2,2);Check(!refused.Success,"annotated table must be guarded");Check(Canon(s.Save())==before,"annotated refusal changed original");
 var table=s.Project().AnchorIndex.Keys.First(k=>k.StartsWith("tbl:body:"));Ok(s.InsertTableColumn(s.ResolveTableCellCoordinate(table,0,0).Cell!.Anchor.Id,Position.After));Check(s.ListComments().Count==1,"other table comment lost");Validate(s.Save());
 using var r=new DocxSession(s.Save());Ok(r.RejectAllRevisions());Check(Canon(r.Save())==before,"outside comments reject changed original");Console.WriteLine("PASS table-local comment guard");
}
using(var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="他人"})){
 Ok(s.InsertTableColumn(Cell(s),Position.After));s.SetRevisionAuthor("验收");var before=Canon(s.Save());var refused=s.SplitCell(Cell(s),2,2);Check(!refused.Success,"foreign revisions must be guarded");Check(Canon(s.Save())==before,"foreign refusal modified package");Console.WriteLine("PASS foreign revision guard");
}
using(var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"})){
 var result=s.SplitCell(Cell(s),20,20);Ok(result);Validate(s.Save());var before=Canon(s.Save());var refused=s.SplitCell(Cell(s),int.MaxValue,int.MaxValue);Check(!refused.Success,"large split must fail");Check(Canon(s.Save())==before,"large split changed package");Console.WriteLine("PASS split size bounds");
 var html=WmlToHtmlConverter.ConvertToHtml(new WmlDocument("tracked.docx",s.Save()),new WmlToHtmlConverterSettings{RenderTrackedChanges=true});
 Check(html.ToString().Contains("row-del")&&html.ToString().Contains("row-ins"),"renderer dropped table revision classes");Console.WriteLine("PASS native renderer row revisions");
}
foreach(var accept in new[]{true,false})foreach(var reverse in new[]{true,false}){
 using var s=new DocxSession(baseline,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});Ok(s.MergeCells(Cell(s),2,2));
 var entries=s.ListRevisions();Console.WriteLine("revision groups "+string.Join(",",entries.Select(e=>$"{e.Family}:{e.ConstituentIds.Count}")));
 using var endpoint=new DocxSession(s.Save());Ok(accept?endpoint.AcceptAllRevisions():endpoint.RejectAllRevisions());
 while(s.ListRevisions().Count>0){var id=(reverse?s.ListRevisions().Last():s.ListRevisions().First()).Id;Ok(accept?s.AcceptRevision(id):s.RejectRevision(id));Validate(s.Save());}
 Check(Canon(s.Save())==Canon(endpoint.Save()),"individual revision resolution endpoint differs");Console.WriteLine($"PASS individual revisions accept={accept} reverse={reverse}");
}
byte[] WithParagraphIds(byte[] input){using var stream=new MemoryStream();stream.Write(input);using(var doc=WordprocessingDocument.Open(stream,true)){var x=doc.MainDocumentPart!.GetXDocument();int id=1;foreach(var p in x.Descendants(W.p))p.SetAttributeValue(W14.paraId,(id++).ToString("X8"));doc.MainDocumentPart.PutXDocument();}return stream.ToArray();}
using(var s=new DocxSession(WithParagraphIds(baseline),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"})){
 var before=Canon(s.Save());Ok(s.InsertTableColumn(Cell(s),Position.After));var modified=s.Save();Validate(modified);var ids=Body(modified).Descendants(W.p).Attributes(W14.paraId).Select(a=>a.Value).ToArray();Check(ids.Distinct().Count()==ids.Length,"table replacement duplicated paragraph IDs");using var r=new DocxSession(modified);Ok(r.RejectAllRevisions());Check(Canon(r.Save())==before,"rejection changed original paragraph IDs");Console.WriteLine("PASS paragraph ID preservation");
}
Console.WriteLine("PASS table engine");
