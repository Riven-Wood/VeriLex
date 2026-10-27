using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using DocumentFormat.OpenXml.Packaging;
using DocumentFormat.OpenXml.Validation;
using Docxodus;

var bytes=File.ReadAllBytes(args[0]);
void Check(bool ok,string message){if(!ok)throw new Exception(message);}
string[] Texts(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);return doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p).Select(p=>string.Concat(p.Descendants(W.t).Select(t=>(string)t))).ToArray();}
void Validate(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);var errors=new OpenXmlValidator(DocumentFormat.OpenXml.FileFormatVersions.Office2016).Validate(doc).ToArray();Check(errors.Length==0,"invalid OOXML: "+string.Join("; ",errors.Select(e=>e.Description)));}
if(args.Length>1){
 Validate(bytes);using var document=new DocxSession(bytes);Check(document.AcceptAllRevisions().Success,"accept UI artifact");
 Validate(document.Save());File.WriteAllBytes(args[1],document.Save());
 using var stream=new MemoryStream(document.Save());using var doc=WordprocessingDocument.Open(stream,false);
 foreach(var p in doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p))
  Console.WriteLine(string.Concat(p.Descendants().Where(e=>e.Name==W.t||e.Name==W.br||e.Name==W.tab).Select(e=>e.Name==W.t?(string)e:e.Name==W.br?"<BR>":"<TAB>")));
 Console.WriteLine("PASS: UI Word package validates and all revisions resolve");return;
}
// Removing one's pending insertion cancels it; only original/other-author text is a deletion.
foreach(var mode in new[]{"all","partial","replace","mixed","mixed-after","mixed-replace"}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.First(k=>k.StartsWith("p:body:"));var original=Texts(bytes)[0];
 var inserted=original.Insert(3,"新增内容");Check(session.SyncKeyboardText(id,inserted).Success,"insert cancellation fixture");
 var expected=mode switch {"all"=>original,"partial"=>original.Insert(3,"新容"),"replace"=>original.Insert(3,"替换"),"mixed"=>original.Remove(2,1).Insert(2,"内容"),"mixed-after"=>original.Remove(3,1).Insert(3,"新增"),_=>original.Remove(2,2).Insert(2,"替换")};
 Check(session.SyncKeyboardText(id,expected).Success,"cancel own insertion "+mode);Validate(session.Save());
 using(var doc=WordprocessingDocument.Open(new MemoryStream(session.Save()),false)){
  var deleted=string.Concat(doc.MainDocumentPart!.GetXDocument().Descendants(W.delText).Select(t=>(string)t));
  Check(deleted==(mode switch {"mixed"=>original.Substring(2,1),"mixed-after"=>original.Substring(3,1),"mixed-replace"=>original.Substring(2,2),_=>""}),"own insertion became deletion: "+mode+" / "+deleted);
 }
 Check(Texts(session.Save())[0]==expected,"cancelled insertion text "+mode);
 Check(session.Undo(),"undo cancellation");Check(Texts(session.Save())[0]==inserted,"undo cancelled input");Check(session.Redo(),"redo cancellation");Validate(session.Save());
 using(var reopened=new DocxSession(session.Save())){Check(reopened.RejectAllRevisions().Success,"reject cancellation revisions");Check(Texts(reopened.Save())[0]==original,"reject returns original "+mode);Validate(reopened.Save());}
 using(var reopened=new DocxSession(session.Save())){Check(reopened.AcceptAllRevisions().Success,"accept cancellation revisions");Check(Texts(reopened.Save())[0]==expected,"accept returns current "+mode);Validate(reopened.Save());}
}
using(var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="甲"})){
 var id=session.Project().AnchorIndex.Keys.First(k=>k.StartsWith("p:body:"));var original=Texts(bytes)[0];
 Check(session.SyncKeyboardText(id,original.Insert(3,"对方新增")).Success,"other author insert");session.SetRevisionAuthor("乙");
 Check(session.SyncKeyboardText(id,original).Success,"other author delete");Check(session.ListRevisions().Any(r=>r.Type=="delete"),"other author deletion must remain reviewable");Validate(session.Save());
}

// Pure insertion leaves every original character untouched, including punctuation.
foreach(var tracked in new[]{false,true})foreach(var location in new[]{0,5,-1}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.First(k=>k.StartsWith("p:body:"));
 var original=Texts(bytes)[0];var offset=location<0?original.Length:location;
 var typed=original.Insert(offset,"新增文字");
 Check(session.SyncKeyboardText(id,typed).Success,"pure insert");Validate(session.Save());
 Check(Texts(session.Save())[0]==typed,"pure insertion offset");
 using(var doc=WordprocessingDocument.Open(new MemoryStream(session.Save()),false)){
  var root=doc.MainDocumentPart!.GetXDocument();
  Check(!root.Descendants(W.del).Any(),"pure insertion created a deletion");
  if(tracked)Check(string.Concat(root.Descendants(W.ins).Descendants(W.t).Select(t=>(string)t))=="新增文字","insertion included original text");
 }
 Check(session.Undo(),"undo pure insert");Check(Texts(session.Save())[0]==original,"undo insertion content");Check(session.Redo(),"redo pure insert");
 Check(session.SyncKeyboardText(id,typed.Insert(offset+1,"继续")).Success,"edit inside insertion");Validate(session.Save());
 if(tracked){using var rejected=new DocxSession(session.Save());Check(rejected.RejectAllRevisions().Success,"reject pure insertion");Check(Texts(rejected.Save())[0]==original,"reject restores original");Validate(rejected.Save());}
}
foreach(var tracked in new[]{false,true})foreach(var offset in new[]{0,5,13})foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).Skip(1).First();
 var baseline=Texts(bytes);
 var result=session.SplitParagraph(id,offset);Check(result.Success,result.Error?.Message??"split");
 Validate(session.Save());
 if(tracked){Check(session.ListRevisions().Count>0,"missing mark");var resolution=accept?session.AcceptAllRevisions():session.RejectAllRevisions();Check(resolution.Success,"resolve");}
 var actual=Texts(session.Save());Validate(session.Save());
 Check(actual.Length==baseline.Length+(!tracked||accept?1:0),"paragraph count");
 Check(string.Concat(actual)==string.Concat(baseline),"split text changed");
 Check(session.Undo(),"undo");Check(session.Redo(),"redo");
}
foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var ids=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ToArray();
 var result=session.MergeParagraphs(ids[1],ids[2]);Check(result.Success,result.Error?.Message??"merge");Validate(session.Save());
 var resolution=accept?session.AcceptAllRevisions():session.RejectAllRevisions();Check(resolution.Success,"merge resolve");
 var actual=Texts(session.Save());Check(actual.Length==(accept?2:3),"merge count");Check(string.Concat(actual)==string.Concat(Texts(bytes)),"merge adds/removes text");Validate(session.Save());
}
using(var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline})){
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).Skip(1).First();
 var split=session.SplitParagraph(id,5);Check(split.Success,"split undo fixture");
 var merge=session.MergeParagraphs(split.Modified[0].Id,split.Created[0].Id);Check(merge.Success,"cancel inserted boundary");
 Check(Texts(session.Save()).SequenceEqual(Texts(bytes)),"insert/delete roundtrip");Validate(session.Save());
}
foreach(var kind in new[]{"br","tab"})foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).Skip(1).First();
 var result=session.InsertKeyboardControl(id,5,kind);Check(result.Success,result.Error?.Message??"control");
 Validate(session.Save());
 var resolution=accept?session.AcceptAllRevisions():session.RejectAllRevisions();Check(resolution.Success,"control resolve");
 using var stream=new MemoryStream(session.Save());using var doc=WordprocessingDocument.Open(stream,false);
 Check(doc.MainDocumentPart!.GetXDocument().Descendants(kind=="br"?W.br:W.tab).Count()==(accept?1:0),"control preservation");
 Check(Texts(session.Save()).SequenceEqual(Texts(bytes)),"control changed text");
}
foreach(var tracked in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).Skip(1).First();
 var expected="第一段用于检查\n软换行后输入\t以及制表符";
 var result=session.SyncKeyboardText(id,expected);Check(result.Success,result.Error?.Message??"sync");
 result=session.SyncKeyboardText(id,expected+"继续输入");Check(result.Success,result.Error?.Message??"second sync");
 Validate(session.Save());
 if(tracked){
  using var rejected=new DocxSession(session.Save());Check(rejected.RejectAllRevisions().Success,"reject keyboard text");
  Check(Texts(rejected.Save()).SequenceEqual(Texts(bytes)),"reject restores original text");Validate(rejected.Save());
  Check(session.AcceptAllRevisions().Success,"accept keyboard text");
 }
 using var stream=new MemoryStream(session.Save());using var doc=WordprocessingDocument.Open(stream,false);
 var p=doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p).Skip(1).First();
 var text=string.Concat(p.Descendants().Where(e=>e.Name==W.t||e.Name==W.br||e.Name==W.tab).Select(e=>e.Name==W.t?(string)e:e.Name==W.br?"\n":"\t"));
 Check(text==expected+"继续输入","keyboard text order changed: "+text);
 Check(session.Undo(),"keyboard undo");Validate(session.Save());
}
using(var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline})){
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).Skip(1).First();
 Check(session.InsertKeyboardControl(id,7,"br").Success,"live break");
 var expected="第一段用于检查\n新增文字回车与空格。";
 for(var repeat=0;repeat<4;repeat++)Check(session.SyncKeyboardText(id,expected).Success,"repeat live sync");
 Validate(session.Save());
 Check(session.AcceptAllRevisions().Success,"accept live break");
 using var stream=new MemoryStream(session.Save());using var doc=WordprocessingDocument.Open(stream,false);
 var paragraph=doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!.Elements(W.p).Skip(1).First();
 var text=string.Concat(paragraph.Descendants().Where(e=>e.Name==W.t||e.Name==W.br).Select(e=>e.Name==W.br?"\n":(string)e));
 Check(text==expected,"repeated sync lost the break: "+text);
}
// The workbench starts with tracking enabled. Table insertion must remain editable,
// export native revisions, and resolve without altering any pre-existing body blocks.
XElement Body(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);return new XElement(doc.MainDocumentPart!.GetXDocument().Root!.Element(W.body)!);}
XElement Numbering(byte[] data){using var stream=new MemoryStream(data);using var doc=WordprocessingDocument.Open(stream,false);return new XElement(doc.MainDocumentPart!.NumberingDefinitionsPart?.GetXDocument().Root??new XElement(W.numbering));}
XElement WithoutEngineIds(XElement body){foreach(var a in body.DescendantsAndSelf().Attributes().Where(a=>a.Name==PtOpenXml.Unid||a.IsNamespaceDeclaration).ToList())a.Remove();return body;}
foreach(var tracked in new[]{false,true})foreach(var index in new[]{1,2})foreach(var position in new[]{Position.Before,Position.After}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(index);
 var result=session.InsertTable(id,position,2,2,new TableInsertOptions{CellContents=new[]{"新表甲","新表乙","新表丙","新表丁"}});
 Check(result.Success,result.Error?.Message??"insert table");Check(result.Created.Count==4,"four new cell anchors");Validate(session.Save());
 var inserted=Body(session.Save());Check(inserted.Elements(W.tbl).Count()==2,"inserted table exists");
 Check(session.Undo(),"undo table insertion");Check(XNode.DeepEquals(WithoutEngineIds(Body(session.Save())),WithoutEngineIds(Body(bytes))),"table undo restores body");Check(session.Redo(),"redo table insertion");Validate(session.Save());
 if(tracked){
  Check(inserted.Descendants(W.trPr).Elements(W.ins).Count()==2,"native table row insertion marks");
  using var rejected=new DocxSession(session.Save());Check(rejected.RejectAllRevisions().Success,"reject table insertion");Validate(rejected.Save());
  Check(XNode.DeepEquals(WithoutEngineIds(Body(rejected.Save())),WithoutEngineIds(Body(bytes))),"reject table restores original body");
  Check(session.AcceptAllRevisions().Success,"accept table insertion");Validate(session.Save());
 }
 Check(Body(session.Save()).Elements(W.tbl).Count()==2,"accepted table remains");
 Check(Texts(session.Save()).Where(t=>t.Length>0).SequenceEqual(Texts(bytes)),"table insertion preserves original paragraphs");
 var freshTable=Body(session.Save()).Elements(W.tbl).Single(t=>string.Concat(t.Descendants(W.t).Select(x=>(string)x)).Contains("新表甲"));
 Check(freshTable.Elements(W.tr).Count()==2&&freshTable.Descendants(W.tc).Count()==4,"accepted table shape");

}
foreach(var tracked in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 var inserted=session.InsertTable(id,Position.After,2,2);Check(inserted.Success,"empty table insertion");Validate(session.Save());
 using var reopened=new DocxSession(session.Save(),new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 // Export strips internal anchors, so resolve the new cells again after reopening.
 var cell=reopened.Project().AnchorIndex.Values.First(a=>a.Anchor.Kind=="tc"&&a.TextPreview.Length==0).Anchor;
 var paragraph=reopened.Project().AnchorIndex.Values.First(a=>a.Anchor.Kind=="p"&&a.TextPreview.Length==0&&a.Anchor.Scope==cell.Scope);
 Check(reopened.SyncKeyboardText(paragraph.Anchor.Id,"表格继续输入").Success,"typing in inserted table");
 Check(reopened.ApplyFormat(paragraph.Anchor.Id,null,new FormatOp{FontFamily="宋体",FontSizePts=18,Bold=true}).Success,"format inserted cell");
 Check(reopened.SetParagraphFormat(paragraph.Anchor.Id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}).Success,"align inserted cell");
 var addRow=reopened.InsertTableRow(cell.Id,Position.After);Check(addRow.Success,"add row in inserted table: "+addRow.Error?.Message);Validate(reopened.Save());
 if(tracked){
  using var rejected=new DocxSession(reopened.Save());Check(rejected.RejectAllRevisions().Success,"reject edited inserted table");Validate(rejected.Save());
  Check(XNode.DeepEquals(WithoutEngineIds(Body(rejected.Save())),WithoutEngineIds(Body(bytes))),"reject edited table restores original body");
  Check(reopened.AcceptAllRevisions().Success,"accept edited inserted table");
 }
 Validate(reopened.Save());
 Check(Body(reopened.Save()).Descendants(W.t).Any(t=>(string)t=="表格继续输入"),"new cell text saved");
 Check(Body(reopened.Save()).Elements(W.tbl).Any(t=>t.Elements(W.tr).Count()==3),"new row saved");
}
// Consecutive format buttons from one reviewer share the earliest paragraph before-image.
foreach(var listFirst in new[]{false,true})foreach(var keepList in new[]{false,true})foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 EditResult Apply(EditResult r){Check(r.Success,r.Error?.Message??"paragraph formatting");if(r.Modified.Count>0)id=r.Modified[0].Id;Validate(session.Save());return r;}
 if(listFirst)Apply(session.ApplyListFormat(id,ListFormat.Bullet));
 Apply(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}));
 Apply(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Right}));
 if(!listFirst)Apply(session.ApplyListFormat(id,ListFormat.Bullet));
 if(!keepList)Apply(session.ApplyListFormat(id,ListFormat.None));
 Apply(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Left}));
 Check(Body(session.Save()).Descendants(W.pPrChange).Count()==1,"one coalesced paragraph revision");
 Check(session.Undo(),"undo repeated paragraph format");Check(session.Redo(),"redo repeated paragraph format");
 // Coalescing survives a saved document being reopened with the same author.
 using var reopened=new DocxSession(session.Save(),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var freshId=reopened.Project().AnchorIndex.Values.First(a=>a.TextPreview.StartsWith("第一段用于检查")&&a.Anchor.Kind is "p" or "li").Anchor.Id;
 Check(reopened.SetParagraphFormat(freshId,new ParagraphFormatOp{Alignment=ParagraphAlignment.Right}).Success,"continue own saved revision");Validate(reopened.Save());
 Check((accept?reopened.AcceptAllRevisions():reopened.RejectAllRevisions()).Success,"resolve repeated paragraph changes");Validate(reopened.Save());
 var body=Body(reopened.Save());
 if(accept){
  var pPr=body.Elements(W.p).ElementAt(1).Element(W.pPr);
  Check((string?)pPr?.Element(W.jc)?.Attribute(W.val)=="right","accepted last alignment");
  Check((pPr?.Element(W.numPr) is not null)==keepList,"accepted list state");
 }
 else Check(XNode.DeepEquals(WithoutEngineIds(body),WithoutEngineIds(Body(bytes))),"reject repeated formatting restores original body");
}
using(var other=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="另一位审阅人"})){
 var id=other.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 Check(other.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}).Success,"other reviewer fixture");
 using var session=new DocxSession(other.Save(),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 var before=WithoutEngineIds(Body(session.Save()));
 Check(!session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Right}).Success,"other reviewer's alignment protected");
 Check(!session.ApplyListFormat(id,ListFormat.Bullet).Success,"other reviewer's list properties protected");
 Check(XNode.DeepEquals(before,WithoutEngineIds(Body(session.Save()))),"other reviewer's markup unchanged");Validate(session.Save());
}
using(var other=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="另一位审阅人"})){
 var id=other.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 Check(other.ApplyListFormat(id,ListFormat.Bullet).Success,"other reviewer numbering fixture");
 using var session=new DocxSession(other.Save(),new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 id=session.Project().AnchorIndex.Values.First(a=>a.Anchor.Kind=="li").Anchor.Id;
 var before=WithoutEngineIds(Body(session.Save()));
 Check(!session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}).Success,"other reviewer's numbering revision protected");
 Check(!session.ApplyListFormat(id,ListFormat.None).Success,"other reviewer's numbering not removed");
 Check(XNode.DeepEquals(before,WithoutEngineIds(Body(session.Save()))),"other reviewer's numbering unchanged");Validate(session.Save());
}
// Resolving individual revisions must not undo a separately accepted list or format.
foreach(var listFirst in new[]{false,true})foreach(var accept in new[]{false,true}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=TrackedChangeMode.RenderInline,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 if(listFirst){var list=session.ApplyListFormat(id,ListFormat.Bullet);Check(list.Success,"selective first list");id=list.Modified[0].Id;}
 Check(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Center}).Success,"selective first format");
 if(!listFirst){var list=session.ApplyListFormat(id,ListFormat.Bullet);Check(list.Success,"selective second list");id=list.Modified[0].Id;}
 Check(session.SetParagraphFormat(id,new ParagraphFormatOp{Alignment=ParagraphAlignment.Right}).Success,"selective second format");
 Validate(session.Save());var changes=session.ListRevisions();Check(changes.Count==1,"one atomic list and paragraph revision");
 Check((accept?session.AcceptRevision(changes[0].Id):session.RejectRevision(changes[0].Id)).Success,"selective resolution");Validate(session.Save());
 var resolved=WithoutEngineIds(Body(session.Save()));
 Check((accept?session.RejectAllRevisions():session.AcceptAllRevisions()).Success,"opposite resolution after selective action");Validate(session.Save());
 Check(XNode.DeepEquals(resolved,WithoutEngineIds(Body(session.Save()))),"resolved list and format stay resolved");
 if(accept){var pPr=resolved.Elements(W.p).ElementAt(1).Element(W.pPr);Check(pPr?.Element(W.numPr) is not null&&(string?)pPr.Element(W.jc)?.Attribute(W.val)=="right","selective accept keeps final list and alignment");}
 else {
  Check(XNode.DeepEquals(resolved,WithoutEngineIds(Body(bytes))),"selective reject restores original body");
  Check(XNode.DeepEquals(WithoutEngineIds(Numbering(session.Save())),WithoutEngineIds(Numbering(bytes))),"selective reject restores numbering definitions");
 }
}
// The frontend groups selection deletion plus Enter/Shift+Enter/paste as one edit.
// Exercise real nested session transactions rather than substituting bridge mocks.
foreach(var tracked in new[]{false,true})foreach(var action in new[]{"paragraph","br","tab","paste"}){
 using var session=new DocxSession(bytes,new DocxSessionSettings{TrackedChanges=tracked?TrackedChangeMode.RenderInline:TrackedChangeMode.Accept,RevisionAuthor="验收"});
 var id=session.Project().AnchorIndex.Keys.Where(k=>k.StartsWith("p:body:")).ElementAt(1);
 const string prefix="第一段用于检查",suffix="与空格。";
 using(var transaction=session.BeginTransaction()){
  Check(session.SyncKeyboardText(id,prefix+suffix).Success,"transaction selection deletion");
  if(action=="paragraph")Check(session.SplitParagraph(id,prefix.Length).Success,"transaction Enter");
  else if(action=="paste"){
   Check(session.SyncKeyboardText(id,prefix+"粘贴一行"+suffix).Success,"transaction paste first line");
   var split=session.SplitParagraph(id,prefix.Length+"粘贴一行".Length);Check(split.Success,"transaction paste split");
   Check(session.SyncKeyboardText(split.Created[0].Id,"粘贴二行"+suffix).Success,"transaction paste second line");
  }
  else Check(session.InsertKeyboardControl(id,prefix.Length,action).Success,"transaction keyboard control");
  transaction.Commit();
 }
 Validate(session.Save());var edited=WithoutEngineIds(Body(session.Save()));
 Check(session.Undo(),"one undo for grouped keyboard edit");Validate(session.Save());
 Check(XNode.DeepEquals(WithoutEngineIds(Body(session.Save())),WithoutEngineIds(Body(bytes))),"one undo restores selection and paragraph structure");
 Check(session.Redo(),"one redo for grouped keyboard edit");Validate(session.Save());
 Check(XNode.DeepEquals(WithoutEngineIds(Body(session.Save())),edited),"one redo restores grouped keyboard edit");
 if(tracked){
  Check(session.RejectAllRevisions().Success,"reject grouped keyboard edit");Validate(session.Save());
  var actual=WithoutEngineIds(Body(session.Save()));var original=WithoutEngineIds(Body(bytes));
  // Native rejection may split one text run into equivalent adjacent runs. Compare
  // the sample's text, paragraph properties, run formatting and table separately.
  Check(Texts(session.Save()).SequenceEqual(Texts(bytes)),"reject grouped "+action+" restores paragraph text and count");
  Check(actual.Elements(W.p).Select(p=>p.Element(W.pPr)?.ToString()??"").SequenceEqual(original.Elements(W.p).Select(p=>p.Element(W.pPr)?.ToString()??"")),"reject grouped edit restores paragraph properties");
  Check(actual.Elements(W.p).SelectMany(p=>p.Descendants(W.rPr)).Select(p=>p.ToString()).SequenceEqual(original.Elements(W.p).SelectMany(p=>p.Descendants(W.rPr)).Select(p=>p.ToString())),"reject grouped edit preserves run formatting");
  Check(XNode.DeepEquals(actual.Elements(W.tbl).Single(),original.Elements(W.tbl).Single()),"reject grouped edit preserves original table");
 }
}
Console.WriteLine("PASS: keyboard transactions, tables, repeated paragraph formatting, atomic selective revisions, accept/reject, reviewer boundaries, undo/redo and valid Word package");
